"""MLPSO package integration checks. All generated inputs/results use QA temp dirs."""
import copy
import hashlib
import itertools
import json
import math
import random
import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from backend.services.result_standardizer import standardize
from backend.solver_v5 import solve_improved_ga_instance as ga_base
from backend.solver_v5 import solve_mlpso_v3_instance as mlpso
from backend.solver_v5 import solve_ss_micro_ga_fast_instance as micro
from backend.solver_v5.benchmark_common import V5ScheduleDecoder as Reference
from backend.solver_v5.benchmark_common_fast import V5ScheduleDecoder as Fast
from backend.tests.test_v5 import instance
from backend.tests.test_micro_integrity import INVALID


def config(**overrides):
    values=dict(population=6, subpops=3, iterations=3, max_decodes=18,
                polish_budget=0, runs=1, seed_base=42, parallel_runs=False,
                progress_every=0)
    return {**values,**overrides}


def test_same_decoder_identity():
    fast, loader=mlpso._decoder_api(True)
    reference, _=mlpso._decoder_api(False)
    assert fast is micro.V5ScheduleDecoder is Fast
    assert reference is Reference
    assert fast.__module__=='backend.solver_v5.benchmark_common_fast'


def test_invalid_candidate_cannot_alias_cached_fitness():
    evaluator=mlpso.BudgetEvaluator(Fast(instance()),12)
    evaluator.cache[(0,1,2,3)]=(123.,(123.,0.,0,123.))
    value=evaluator.evaluate((False,1,2,3))
    assert math.isinf(value[0])
    assert evaluator.new_decodes==0


def test_local_search_retains_last_decode_improvement(monkeypatch):
    decoder=Fast(instance())
    evaluator=mlpso.BudgetEvaluator(decoder,1)
    monkeypatch.setattr(decoder,'decode_indices',lambda ch:(10.,0.,0,10.))
    monkeypatch.setattr(mlpso,'swap_mutation',lambda *a:(1,0,2,3))
    monkeypatch.setattr(mlpso,'insert_mutation',lambda *a:(2,0,1,3))
    monkeypatch.setattr(mlpso,'repair_product_change_remainders',lambda ch,dec:ch)
    ch,value,improved,stopped=mlpso.local_search_gbest(
        (0,1,2,3),100.,decoder,evaluator,random.Random(42),2,1)
    assert improved and stopped
    assert value==10. and ch==(1,0,2,3)


def test_all_infeasible_returns_diagnostic_not_a_solver_crash(tmp_path,monkeypatch):
    monkeypatch.setattr(mlpso,'_decoder_api',lambda fast:(Fast,micro.load_instance))
    data=instance()
    data['metadata']['horizon_end_min']=1
    for windows in data['resource_windows'].values():
        for window in windows:window['end_min']=1
    path=tmp_path/'too-short.json';path.write_text(json.dumps(data))
    result=mlpso.solve_instance(path,**config(iterations=0))
    assert result['feasible'] is False
    assert result['best_makespan_min'] is None
    assert result['schedule']==[]
    assert result['run_summaries']
    json.dumps(result,allow_nan=False)


@pytest.mark.parametrize('chromosome',INVALID)
def test_invalid_permutation_is_not_decoded_or_cached(monkeypatch,chromosome):
    decoder=Fast(instance())
    monkeypatch.setattr(decoder,'decode_indices',lambda ch:pytest.fail('Invalid particle reached decoder'))
    evaluator=mlpso.BudgetEvaluator(decoder,6)
    evaluator.cache[(0,1,2,3)]=(123.,(123.,0.,0,123.))
    before=dict(evaluator.cache)
    assert not evaluator.can_decode(chromosome)
    assert math.isinf(evaluator.evaluate(chromosome)[0])
    assert evaluator.cache==before and evaluator.new_decodes==0


def test_cache_and_budget_count_actual_new_evaluations_only():
    evaluator=mlpso.BudgetEvaluator(Fast(instance()),1)
    first=evaluator.evaluate([0,1,2,3])
    assert evaluator.new_decodes==1
    assert evaluator.evaluate((0,1,2,3))==first
    assert evaluator.new_decodes==1 and evaluator.cache_hits==1
    assert evaluator.evaluate((0,2,1,3)) is None
    assert evaluator.best_ch==(0,1,2,3)


def test_same_chromosome_has_same_fitness_and_canonical_schedule():
    data=instance();before=copy.deepcopy(data)
    a=mlpso._decoder_api(True)[0](data);b=micro.V5ScheduleDecoder(data)
    assert a.data==b.data==before and a.windows==b.windows
    evaluator=mlpso.BudgetEvaluator(a,24)
    for values in itertools.permutations(range(4)):
        ch=bytes(values)
        fit,metrics=evaluator.evaluate(ch)
        key,ga_metrics,_=ga_base.evaluate_one(ch,b,{})
        assert metrics==pytest.approx(ga_metrics) and fit==pytest.approx(key[0])
        if math.isfinite(fit):
            ids=[a.jobs[i] for i in ch]
            m1,s1=a.decode(ids,keep_schedule=True)
            m2,s2=b.decode(ids,keep_schedule=True)
            assert m1==m2 and s1==s2
            assert m1['makespan_min']==pytest.approx(fit)
    assert data==before


@pytest.mark.parametrize('operator',['swap','insert','block','double','reverse','mbx','pmx','guided','mixed','learn','screened'])
def test_operator_repair_decode_fitness_pipeline(operator):
    data=instance();original=copy.deepcopy(data)
    decoder=Fast(data);reference=Reference(data);rng=random.Random(42)
    evaluator=mlpso.BudgetEvaluator(decoder,48)
    current=bytes(range(4));guide=bytes(reversed(range(4)))
    for _ in range(12):
        if operator=='pmx':child=mlpso.pmx_crossover(current,guide,rng)
        elif operator=='guided':child=mlpso.guided_insert_move(current,guide,rng)
        elif operator=='learn':child=mlpso.learn_from_guide(current,guide,decoder,rng)
        elif operator=='screened':child=mlpso.screened_particle_candidate(current,current,guide,decoder,rng,.8,.8,.4,2)
        else:
            function={'swap':mlpso.swap_mutation,'insert':mlpso.insert_mutation,
                'block':mlpso.block_insert_mutation,'double':mlpso.double_insert_mutation,
                'reverse':mlpso.segment_reverse_mutation,'mbx':mlpso.moving_based_crossover,
                'mixed':mlpso.mixed_mutation}[operator]
            child=function(current,rng)
        assert decoder.valid_chromosome_indices(child)
        repaired=mlpso.repair_product_change_remainders(child,decoder)
        assert repaired==ga_base.repair_product_change_remainders(child,decoder)
        assert decoder.valid_chromosome_indices(repaired)
        assert evaluator.evaluate(repaired)[1]==pytest.approx(reference.decode_indices(repaired))
        current=repaired
    assert data==original


def test_polish_terminates_when_its_neighborhood_is_entirely_cached(monkeypatch):
    decoder=Fast(instance());evaluator=mlpso.BudgetEvaluator(decoder,100)
    monkeypatch.setattr(decoder,'decode_indices',lambda ch:(2000.,0.,0,2000.))
    candidate=(1,0,2,3);evaluator.evaluate(candidate)
    for name in ['insert_mutation','swap_mutation','block_insert_mutation',
                 'guided_insert_move','double_insert_mutation','segment_reverse_mutation']:
        monkeypatch.setattr(mlpso,name,lambda *args,**kwargs:candidate)
    monkeypatch.setattr(mlpso,'repair_product_change_remainders',lambda ch,dec:ch)
    rng=random.Random(42);monkeypatch.setattr(rng,'random',lambda:1.)
    result=mlpso.elite_polish_phase((0,1,2,3),1000.,[],decoder,evaluator,
        rng,2,1,2,1,mlpso.time.perf_counter())
    assert result['stopped_no_new_decodes']
    assert result['rounds']<=32 and evaluator.new_decodes==1


def test_run_returns_every_evaluated_incumbent_even_if_local_state_misses_it(tmp_path,monkeypatch):
    path=tmp_path/'input.json';path.write_text(json.dumps(instance()))
    def local(*args,**kwargs):
        # Complete real physical evaluations, then simulate a lost local return.
        evaluator=args[3]
        for ch in itertools.permutations(range(4)):
            if evaluator.evaluate(ch) is None:break
        return args[0],args[1],False,True
    monkeypatch.setattr(mlpso,'local_search_gbest',local)
    raw=mlpso.solve_instance(path,**config(ls_every=1,iterations=2,max_decodes=24))
    dec=Fast(instance())
    values=[dec.decode_indices(ch)[0] for ch in itertools.permutations(range(4))]
    assert raw['best_makespan_min']==pytest.approx(min(values))
    assert raw['feasible']


def test_migration_preserves_global_historical_elite():
    positions=[(0,1,2),(1,0,2),(2,0,1),(2,1,0)]
    fitness=[10.,20.,30.,40.];pbest=list(positions);pfit=list(fitness)
    mlpso.collaborative_migration([[0,1],[2,3]],positions,fitness,pbest,pfit)
    assert min(pfit)==10. and pbest[0]==(0,1,2)
    assert pfit[3]==10. and pbest[3]==(0,1,2)


def test_existing_search_operators_and_normal_search_are_preserved(tmp_path,monkeypatch):
    source=Path(r'D:\back up giga\thesis\flowlien dataset\correct workstation\fast decode\solve_mlpso_v3_instance.py')
    original_ast=ast.parse(source.read_text(encoding='utf-8-sig'))
    packaged_ast=ast.parse(Path(mlpso.__file__).read_text(encoding='utf-8-sig'))
    original={node.name:node for node in original_ast.body if isinstance(node,ast.FunctionDef)}
    packaged={node.name:node for node in packaged_ast.body if isinstance(node,ast.FunctionDef)}
    preserved=['_pack','repair_product_change_remainders','structural_surrogate','swap_mutation',
        'insert_mutation','block_insert_mutation','guided_insert_move','mixed_mutation',
        'moving_based_crossover','pmx_crossover','learn_from_guide','_deterministic_seeds',
        'create_initial_swarm','divide_equal_subpopulations','collaborative_migration',
        'double_insert_mutation','segment_reverse_mutation','screened_particle_candidate']
    for name in preserved:
        assert ast.dump(original[name],include_attributes=False)==ast.dump(packaged[name],include_attributes=False),name
    spec=importlib.util.spec_from_file_location('original_v3_audit',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    # Controlled comparison: original search runs against the SAME App decoder.
    monkeypatch.setattr(module,'_decoder_api',mlpso._decoder_api)
    path=tmp_path/'input.json';path.write_text(json.dumps(instance()))
    settings=config(iterations=1,max_decodes=12,ls_every=0,migration_every=1)
    before=module.solve_instance(path,**settings);after=mlpso.solve_instance(path,**settings)
    assert after['best_chromosome']==before['best_chromosome']
    assert after['schedule']==before['schedule']
    for name in ['unique_evaluations','makespan_min','migration_events']:
        assert after['run_summaries'][0][name]==before['run_summaries'][0][name]


def test_multiple_attempts_keep_seed_schedule_and_kpis_reproducible(tmp_path):
    path=tmp_path/'input.json';path.write_text(json.dumps(instance()))
    settings=config(iterations=1,max_decodes=12,runs=2)
    first=mlpso.solve_instance(path,**settings);second=mlpso.solve_instance(path,**settings)
    assert first['seeds']==second['seeds']==[42,1051]
    assert first['best_chromosome']==second['best_chromosome']
    assert first['schedule']==second['schedule']
    assert [r['makespan_min'] for r in first['run_summaries']]==[r['makespan_min'] for r in second['run_summaries']]
    assert first['best_makespan_min']==min(r['makespan_min'] for r in first['run_summaries'])


@pytest.mark.parametrize('impossible',[False,True])
def test_package_cli_exports_valid_json_and_csv_without_external_imports(tmp_path,impossible):
    data=instance()
    if impossible:
        data['metadata']['horizon_end_min']=1
        for windows in data['resource_windows'].values():windows[0]['end_min']=1
    path=tmp_path/'input.json';path.write_text(json.dumps(data));output=tmp_path/'output/result.json'
    completed=subprocess.run([sys.executable,'-B','-m','backend.solver_v5.solve_mlpso_v3_instance',
        '--instance',str(path),'--output',str(output),'--runs','1','--sequential-runs',
        '--population','6','--iterations','0','--max-decodes','6','--polish-budget','0',
        '--progress-every','0'],capture_output=True,text=True,timeout=20)
    assert completed.returncode==0,completed.stderr
    raw=json.loads(output.read_text());assert raw['feasible'] is not impossible
    assert raw['decoder_module']=='backend.solver_v5.benchmark_common_fast'
    assert output.with_name('result_run_summary.csv').exists()
    assert output.with_name('result_convergence.csv').exists()
    assert output.with_name('result_schedule.csv').exists() is not impossible


def test_reference_decoder_option_uses_app_reference(tmp_path):
    path=tmp_path/'input.json';path.write_text(json.dumps(instance()))
    raw=mlpso.solve_instance(path,**config(iterations=0,max_decodes=6,use_fast_decoder=False))
    assert raw['decoder_module']=='backend.solver_v5.benchmark_common'
    assert standardize('MLPSO V3',raw,instance())['feasible']


@pytest.fixture(scope='module')
def small_result(tmp_path_factory):
    data=instance()
    # Saved resource gaps are real test inputs, not solver-side calendar changes.
    for rid in data['resource_windows']:
        data['resource_windows'][rid]=[dict(window_id=f'{rid}_{i}',calendar_date='2026-10-01',
            shift_id='A',start_min=a,end_min=b) for i,(a,b) in enumerate([(0,12),(20,30),(36,1440)])]
    path=tmp_path_factory.mktemp('mlpso-fixture')/'input.json';path.write_text(json.dumps(data))
    raw=mlpso.solve_instance(path,**config(iterations=1,max_decodes=12))
    assert raw['feasible']
    checked=standardize('MLPSO V3',raw,data)
    assert checked['feasible'],checked['validation_issues']
    return data,raw


def test_fixture_constraints_and_repeatability(small_result,tmp_path):
    data,raw=small_result
    assert {'JOB_OP','SETUP','OIL_CYCLE','2C_BATCH'}=={r['Event_Type'] for r in raw['schedule']}
    assert any(r.get('Batch_Composition')=='POOLED_5+5' for r in raw['schedule'])
    path=tmp_path/'input.json';path.write_text(json.dumps(data))
    again=mlpso.solve_instance(path,**config(iterations=1,max_decodes=12))
    assert again['best_chromosome']==raw['best_chromosome']
    assert again['schedule']==raw['schedule']
    assert again['run_summaries'][0]['unique_evaluations']==raw['run_summaries'][0]['unique_evaluations']
    json.dumps(raw,allow_nan=False)


@pytest.mark.parametrize('corruption',['missing_operation','precedence','resource_assignment',
    'oil_phase','oil_quantity','batch_key','batch_composition','missing_setup','calendar_gap','cmax'])
def test_independent_validator_rejects_corrupted_mlpso_schedule(small_result,corruption):
    data,result=small_result;raw=copy.deepcopy(result);rows=raw['schedule']
    if corruption=='missing_operation':rows.remove(next(r for r in rows if r.get('Operation_Code')=='SCALE'))
    elif corruption=='precedence':
        row=next(r for r in rows if r.get('Operation_Code')=='PRE');row['Start_Min']=0;row['Completion_Min']=row['Processing_Min']
    elif corruption=='resource_assignment':next(r for r in rows if r.get('Operation_Code')=='SCALE')['Resource_Units']='OP_PACK'
    elif corruption=='oil_phase':next(r for r in rows if r['Event_Type']=='OIL_CYCLE')['W4_Load_Min']+=1
    elif corruption=='oil_quantity':next(r for r in rows if r['Event_Type']=='OIL_CYCLE')['Cycle_Qty']=7
    elif corruption=='batch_key':next(r for r in rows if r['Event_Type']=='2C_BATCH')['Batch_Key']='wrong-key'
    elif corruption=='batch_composition':next(r for r in rows if r.get('Batch_Composition')=='POOLED_5+5')['Job_ID']='J002'
    elif corruption=='missing_setup':rows.remove(next(r for r in rows if r['Event_Type']=='SETUP'))
    elif corruption=='calendar_gap':
        row=next(r for r in rows if r.get('Operation_Code')=='2B');row['Start_Min']=13;row['Completion_Min']=13+row['Processing_Min']
    elif corruption=='cmax':raw['best_makespan_min']+=1
    assert not standardize('MLPSO V3',raw,data)['feasible']


FROZEN=Path('.tmp-qa/pilot-v5-readonly-20261108T032956Z')
@pytest.mark.parametrize('size,digest',[
    (20,'62d8d830242ae6948fee677c9706a77feb24a1d00db5bd70c62b6fc572c2b8f8'),
    (40,'78ff8981bea147558dba7ee6cd3bf5e24d997100b9c712e41592f76b4b4e38b4')])
def test_original_frozen_smoke(size,digest,tmp_path):
    path=FROZEN/f'n{size:03}'/'frozen.json';payload=path.read_bytes()
    assert hashlib.sha256(payload).hexdigest()==digest
    data=json.loads(payload)
    raw=mlpso.solve_instance(path,**config(iterations=4,max_decodes=60,
        migration_every=1,ls_every=1,polish_budget=12,convergence_every=1))
    assert raw['feasible'] and raw['decoder_module']==micro.V5ScheduleDecoder.__module__
    assert raw['objective']=='MIN_CMAX_FINAL_2C' and raw['input_sha256']==digest
    dec=Fast(data)
    assert len(raw['best_chromosome'])==size and set(raw['best_chromosome'])==set(dec.jobs)
    metrics,schedule=dec.decode(raw['best_chromosome'],keep_schedule=True)
    assert schedule==raw['schedule']
    assert metrics['makespan_min']==pytest.approx(raw['best_makespan_min'],abs=1e-6)
    ga_key,ga_metrics,_=ga_base.evaluate_one(tuple(dec.job_index[j] for j in raw['best_chromosome']),micro.V5ScheduleDecoder(data),{})
    assert ga_key[0]==pytest.approx(metrics['makespan_min'],abs=1e-6)
    checked=standardize('MLPSO V3',raw,data)
    assert checked['feasible'],checked['validation_issues']
    batch_jobs={j for r in schedule if r['Event_Type']=='2C_BATCH' for j in r['Job_ID'].split(';')}
    assert batch_jobs==set(dec.jobs)
    summary=raw['run_summaries'][0]
    assert summary['unique_evaluations']<=60
    final=raw['convergence_by_run'][0][-1]
    assert final['Iteration']==summary['iterations_completed']
    assert final['Decoder_Evaluations']==summary['unique_evaluations']
    assert final['Gbest_Cmax_Min']==pytest.approx(raw['best_makespan_min'])
    assert final['Stop_Reason']==summary['stop_reason']
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
    evidence={'instance':data['metadata']['instance_name'],'input_sha256':digest,'jobs':size,
        'feasible':True,'validation_issues':checked['validation_issues'],'cmax':raw['best_makespan_min'],
        'runtime_sec':raw['total_runtime_sec'],'evaluations':summary['unique_evaluations'],
        'stop_reason':summary['stop_reason'],'events':len(schedule),'decoder_module':raw['decoder_module']}
    (tmp_path/'verification.json').write_text(json.dumps(evidence,indent=2))
    (tmp_path/'raw.json').write_text(json.dumps(raw,allow_nan=False))
    (tmp_path/'validated.json').write_text(json.dumps(checked,allow_nan=False))
