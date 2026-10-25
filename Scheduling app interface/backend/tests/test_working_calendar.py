import copy
import json
from datetime import datetime, timedelta

import pandas as pd
import pytest
from backend.models.schemas import ScheduleRequest, SolverSettings
from backend.services import v5_service
from backend.services.working_calendar import apply_to_new_instance, POLICY
from backend.tests.test_v5 import instance


def weekly_instance():
    data = instance()
    origin = datetime(2026, 10, 3, 22)  # Saturday
    data['metadata'].update(origin_datetime=str(origin), horizon_end_datetime=str(origin+timedelta(days=60)), horizon_end_min=60*1440)
    for windows in data['resource_windows'].values():
        windows[0].update(start_min=0, end_min=60*1440, start_datetime=str(origin), end_datetime=str(origin+timedelta(days=60)))
    return data


def assert_weekly_calendar(data):
    origin=datetime.fromisoformat(data['metadata']['origin_datetime'])
    for windows in data['resource_windows'].values():
        for w in windows:
            a,b=map(datetime.fromisoformat,(w['start_datetime'],w['end_datetime']))
            assert a==origin+timedelta(minutes=w['start_min'])
            assert b==origin+timedelta(minutes=w['end_min'])
            cursor=a.replace(hour=0,minute=0,second=0,microsecond=0)
            while cursor<b:
                if cursor.weekday()==6:
                    assert b<=cursor or a>=cursor+timedelta(days=1,hours=6)
                if cursor.weekday()==0:
                    assert b<=cursor or a>=cursor+timedelta(hours=6)
                cursor+=timedelta(days=1)


def test_sixty_days_weekly_boundaries_and_idempotence():
    data=weekly_instance();apply_to_new_instance(data)
    assert_weekly_calendar(data)
    windows=data['resource_windows']['WS_OIL']
    assert windows[0]['end_datetime']=='2026-10-04 00:00:00'
    assert windows[1]['start_datetime']=='2026-10-05 06:00:00'
    assert windows[-1]['end_datetime']==data['metadata']['horizon_end_datetime']
    assert data['metadata']['horizon_end_min']==86400
    assert len({w['window_id'] for w in windows})==len(windows)
    before=copy.deepcopy(data);apply_to_new_instance(data);assert data==before


def test_freeze_corrects_existing_uploaded_calendar_without_mutating_source(tmp_path):
    source=weekly_instance();before=copy.deepcopy(source)
    historical=tmp_path/'historical.json';historical.write_text(json.dumps(source));saved=historical.read_bytes()
    req=ScheduleRequest(dataset_id='old',job_ids=[j['job_id'] for j in source['jobs']],horizon_start='2026-10-03T23:00',horizon_end='2026-12-02T21:00')
    frozen=v5_service.freeze(source,req)
    assert source==before and historical.read_bytes()==saved
    assert frozen['metadata']['working_calendar_policy']==POLICY
    assert frozen['resource_windows']['WS_OIL'][0]['start_datetime']=='2026-10-03 23:00:00'
    assert frozen['resource_windows']['WS_OIL'][-1]['end_datetime']=='2026-12-02 21:00:00'
    assert_weekly_calendar(frozen)
    assert v5_service.validate(frozen)==[]
    for key in ['jobs','job_operations','precedence','oil_cycles','setup_matrix','batching','resource_by_operation']:
        assert frozen[key]==source[key]


def test_generated_shifts_preserve_breaks_and_resource_downtime_for_sixty_days():
    from backend.solver_v5.generate_benchmark_instances import build_resource_windows
    origin=datetime(2026,10,1,6);end=origin+timedelta(days=60)
    rows=[]
    for day in range(30):
        for shift in range(3):
            start=origin+timedelta(days=day,hours=8*shift)
            rows.append(dict(Shift_Start=start,Shift_End=start+timedelta(hours=8),Break_Start=start+timedelta(hours=4),Break_End=start+timedelta(hours=4,minutes=30),Shift_ID=f'S{shift+1}',Staffed=1))
    data={'shift':pd.DataFrame(rows),'workstations':pd.DataFrame({'Workstation_ID':['WS_A','WS_B']}),'operators':pd.DataFrame({'Operator_ID':['OP_A']}),
          'machine_downtime':pd.DataFrame([{'Workstation_ID':'WS_A','Start':datetime(2026,11,2,7),'End':datetime(2026,11,2,8)}]),
          'operator_downtime':pd.DataFrame([{'Operator_ID':'OP_A','Start':datetime(2026,10,6,8),'End':datetime(2026,10,6,9)}])}
    source=build_resource_windows(data,origin,end)
    generated={'metadata':{'origin_datetime':str(origin),'horizon_end_datetime':str(end),'horizon_end_min':86400},'resource_windows':copy.deepcopy(source)}
    apply_to_new_instance(generated);assert_weekly_calendar(generated)
    for resource,windows in generated['resource_windows'].items():
        for w in windows:
            assert any(s['start_min']<=w['start_min']<w['end_min']<=s['end_min'] for s in source[resource])
        assert any(w['start_min']>30*1440 for w in windows)
    for resource,day,a,b in [('WS_A',32,60,120),('OP_A',5,120,180),('WS_B',32,240,270)]:
        assert not any(w['start_min']<day*1440+b and w['end_min']>day*1440+a for w in generated['resource_windows'][resource])
    assert any(w['start_min']<=32*1440+60 and w['end_min']>=32*1440+120 for w in generated['resource_windows']['WS_B'])


def test_nonpreemptive_slots_cross_adjacent_shifts_but_not_weekly_shutdown():
    from backend.solver_v5.benchmark_common import V5ScheduleDecoder
    data=weekly_instance();apply_to_new_instance(data)
    dec=V5ScheduleDecoder(data)
    # A three-hour operation cannot start Saturday 23:00; it waits for Monday 06:00.
    start,end=dec._find_joint_slot(['WS_PRE_JIG','OP_PRE'],60,180,{})
    assert (start,end)==(1920,2100)
    # Adjacent calendar segments at 14:00 do not force a split.
    for resource in ['WS_PRE_JIG','OP_PRE']:
        data['resource_windows'][resource]=[dict(window_id='a',start_min=1920,end_min=2400),dict(window_id='b',start_min=2400,end_min=2880)]
    dec=V5ScheduleDecoder(data)
    assert dec._find_joint_slot(['WS_PRE_JIG','OP_PRE'],2340,120,{})==(2340,2460)


@pytest.mark.parametrize('algorithm',['CP-SAT','SS Micro-GA Fast'])
def test_new_schedule_revalidated_against_corrected_calendar(tmp_path,algorithm):
    from backend.services.algorithms.v5_adapter import solve_exact,solve_micro
    data=weekly_instance()
    for job in data['jobs']:
        job.update(release_min=110,element_ready_min=110,loopset_release_min=110)
    req=ScheduleRequest(dataset_id='new',job_ids=[j['job_id'] for j in data['jobs']],horizon_start=data['metadata']['origin_datetime'],horizon_end=data['metadata']['horizon_end_datetime'])
    frozen=v5_service.freeze(data,req);assert_weekly_calendar(frozen)
    path=tmp_path/'instance.json';path.write_text(json.dumps(frozen));before=path.read_bytes()
    settings=SolverSettings(exact_time_limit=5,exact_workers=1,micro_population=4,micro_generations=2,micro_max_decodes=100).model_dump()
    raw=(solve_exact if algorithm=='CP-SAT' else solve_micro)(path,settings)
    result=v5_service.standardize(algorithm,raw,frozen)
    assert result['feasible'],result['validation_issues']
    assert result['metadata']['working_calendar_policy']==POLICY
    assert result['objective']=='MIN_CMAX_FINAL_2C'
    assert any(o['startMin']>=1920 for o in result['operations'])
    assert all(o['endMin']<=120 or o['startMin']>=1920 for o in result['operations'])
    assert path.read_bytes()==before
    # A real result against the historical all-days calendar must be rejected
    # if it occupies weekly shutdown in the corrected instance.
    uncorrected=tmp_path/'old.json';uncorrected.write_text(json.dumps(data))
    old_raw=solve_micro(uncorrected,settings)
    assert v5_service.standardize('SS Micro-GA Fast',old_raw,data)['feasible']
    assert not v5_service.standardize('SS Micro-GA Fast',old_raw,frozen)['feasible']
