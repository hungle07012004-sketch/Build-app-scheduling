"""Bounded local-neighborhood experiment, NOT a production GA replacement.

python -B -m experiments.ordering_sensitivity.run --output experiments/ordering_sensitivity/run-NEW
All arms start at the SAME saved incumbent. No source/database writes.
"""
from __future__ import annotations
import argparse
import collections
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sqlite3
import time

RUN_ID='236e9e6fbb1044fab37acb6064f8b4b2'
ARMS=('control','release','pair','combined')


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def random_move(ch,rng):
    seq=list(ch);a,b=rng.sample(range(len(seq)),2)
    if rng.random()<.5:seq[a],seq[b]=seq[b],seq[a]
    else:seq.insert(b,seq.pop(a))
    return tuple(seq)


def release_move(ch,decoder,rng):
    """Swap release inversions in the existing product slots: no extra switches.

    Readiness here uses input release/material/loopset, not a replacement
    scheduling rule. Only the unchanged decoder decides actual feasibility.
    """
    def ready(g):
        j=decoder.jobs[g]
        return max(decoder.release[j],decoder.element[j],decoder.loop[j])
    inversions=[(i,k) for i in range(len(ch)) for k in range(i+1,len(ch))
        if decoder.product[decoder.jobs[ch[i]]]==decoder.product[decoder.jobs[ch[k]]]
        and ready(ch[i])>ready(ch[k])+1e-9]
    if not inversions:return None
    a,b=rng.choice(inversions);seq=list(ch);seq[a],seq[b]=seq[b],seq[a]
    return tuple(seq)


def pair_move(ch,decoder,rng):
    """Move existing same-key remainder partners together, preserving all jobs.

    Pair choice is experimental search only; decoder still owns physical 2C
    grouping. No input quantities/keys or exact-10 rules are changed.
    """
    positions=collections.defaultdict(list)
    for i,g in enumerate(ch):
        j=decoder.jobs[g]
        if decoder.qty[j]%10==5:positions[decoder.batch_key[j]].append(i)
    pairs=[(values[i],values[i+1]) for values in positions.values()
        for i in range(0,len(values)-1,2) if values[i+1]-values[i]>1]
    if not pairs:return None
    a,b=rng.choice(pairs);seq=list(ch)
    if rng.random()<.5:seq.insert(a+1,seq.pop(b))
    else:seq.insert(b-1,seq.pop(a))
    return tuple(seq)


def diagnostics(checked,data):
    jobs={j['job_id']:j for j in data['jobs']};ops=checked['operations']
    pre={o['jobId']:o['startMin'] for o in ops if o['operation']=='PRE'}
    visual={o['jobId']:o['endMin'] for o in ops if o['operation']=='VISUAL'}
    done={}
    for o in ops:
        if o['eventType']=='2C_BATCH':
            for j in o['jobIds']:done[j]=max(done.get(j,0),o['endMin'])
    assert set(pre)==set(visual)==set(done)==set(jobs)
    return dict(tardy_jobs=checked['tardy_jobs'],total_tardiness_min=checked['total_tardiness_min'],
        total_flow_time_min=checked['total_flow_time_min'],
        late_before_pre=sum(pre[j]>jobs[j]['due_min']+1e-6 for j in jobs),
        mean_release_to_pre_hours=sum(pre[j]-jobs[j]['release_min'] for j in jobs)/len(jobs)/60,
        max_visual_to_2c_hours=max(done[j]-visual[j] for j in jobs)/60,
        setup_count=sum(o['eventType']=='SETUP' for o in ops))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--budget',type=int,default=40)
    parser.add_argument('--seeds',type=int,nargs='+',default=[42,1051,2060])
    args=parser.parse_args();root=Path(__file__).resolve().parents[2]
    out=args.output.resolve();base=Path(__file__).resolve().parent
    if out.exists() or not out.is_relative_to(base) or out==base:raise ValueError('Use a NEW experiment subfolder')
    if not 2<=args.budget<=100:raise ValueError('This smoke experiment permits 2..100 search calls per arm')
    out.mkdir(parents=True);os.environ['SCHEDULING_DATA_DIR']=str(out/'unused-data')
    from backend.solver_v5.benchmark_common_fast import V5ScheduleDecoder
    from backend.solver_v5.solve_improved_ga_instance import repair_product_change_remainders
    from backend.services.result_standardizer import standardize
    protected={str(p.relative_to(root)):sha(p) for folder in ('backend','src','code') for p in (root/folder).rglob('*')
        if p.is_file() and p.suffix in ('.py','.tsx','.ts','.css') and 'storage' not in p.parts}
    snapshot=root/'experiments/due_date_sensitivity/run-20261110-01/saved_schedules_snapshot.sqlite3'
    snapshot_sha=sha(snapshot)
    conn=sqlite3.connect(snapshot.as_uri()+'?mode=ro&immutable=1',uri=True)
    path,result,digest=conn.execute('select instance_path,result,instance_sha256 from runs where id=?',(RUN_ID,)).fetchone();conn.close()
    source=Path(path);payload=source.read_bytes()
    if hashlib.sha256(payload).hexdigest()!=digest:raise ValueError('Saved input hash mismatch')
    (out/'frozen.json').write_bytes(payload);data=json.loads(payload)
    original=json.loads(result)['selected'];dec=V5ScheduleDecoder(data)
    start=tuple(dec.job_index[j] for j in original['metadata']['best_chromosome'])
    assert dec.valid_chromosome_indices(start)
    full,rows=dec.decode([dec.jobs[g] for g in start],keep_schedule=True)
    baseline=standardize('SS Micro-GA Fast',{'schedule':rows,**full,'feasible':True},data)
    if not baseline['feasible']:raise ValueError(baseline['validation_issues'])
    for k in ('makespan_min','total_tardiness_min','total_flow_time_min','tardy_jobs'):
        if abs(baseline[k]-original[k])>1e-5:raise ValueError('Baseline differs from saved '+k)
    (out/'baseline.json').write_text(json.dumps(dict(run_id=RUN_ID,cmax_min=baseline['makespan_min'],**diagnostics(baseline,data)),indent=2))
    results=[];traces=[]
    for seed in args.seeds:
        for arm in ARMS:
            tick=time.perf_counter();rng=random.Random(seed);best=start;best_metrics=dec.decode_indices(start)
            best_cmax=best_metrics[0];seen={start};calls=1;attempts=0;fallbacks=0;invalid=0;cache_skips=0;improvements=0
            lowest_tardy=best_metrics[2];lowest_tardy_cmax=best_cmax
            trace=[dict(evaluation=1,best_cmax_min=best_cmax)]
            while calls<args.budget and attempts<args.budget*200:
                attempts+=1;operator=arm
                if arm=='combined':operator='release' if rng.random()<.5 else 'pair'
                child=release_move(best,dec,rng) if operator=='release' else pair_move(best,dec,rng) if operator=='pair' else random_move(best,rng)
                if child is None:
                    fallbacks+=1;child=random_move(best,rng)
                child=repair_product_change_remainders(child,dec)
                if not dec.valid_chromosome_indices(child):raise AssertionError('Operator/repair produced invalid permutation')
                if child in seen:
                    cache_skips+=1;continue
                seen.add(child);metrics=dec.decode_indices(child);calls+=1
                if not math.isfinite(metrics[0]):invalid+=1
                if metrics[0]<best_cmax-1e-9:
                    best=child;best_metrics=metrics;best_cmax=metrics[0];improvements+=1
                if math.isfinite(metrics[0]) and (metrics[2],metrics[0])<(lowest_tardy,lowest_tardy_cmax):
                    lowest_tardy=metrics[2];lowest_tardy_cmax=metrics[0]
                trace.append(dict(evaluation=calls,best_cmax_min=best_cmax))
            search_sec=time.perf_counter()-tick
            if calls!=args.budget:raise AssertionError('Arm did not consume equal search-call budget')
            metrics,raw=dec.decode([dec.jobs[g] for g in best],keep_schedule=True)
            checked=standardize('Experimental ordering',{'schedule':raw,**metrics,'feasible':True},data)
            if not checked['feasible']:raise ValueError(checked['validation_issues'])
            if abs(checked['makespan_min']-best_cmax)>1e-6:raise AssertionError('Reconstruction Cmax mismatch')
            row=dict(seed=seed,arm=arm,jobs=len(dec.jobs),search_decodes=calls,reconstruction_decodes=1,
                independent_validator=True,feasible=True,cmax_min=best_cmax,
                improvement_pct=100*(baseline['makespan_min']-best_cmax)/baseline['makespan_min'],
                **diagnostics(checked,data),search_sec=search_sec,end_to_end_sec=time.perf_counter()-tick,
                improvements=improvements,infeasible_candidates=invalid,generation_attempts=attempts,
                duplicate_skips=cache_skips,random_fallbacks=fallbacks,
                lowest_tardy_evaluated=lowest_tardy,cmax_at_lowest_tardy_evaluated=lowest_tardy_cmax)
            results.append(row);traces.append(dict(seed=seed,arm=arm,trace=trace))
            (out/f'{arm}-{seed}.json').write_text(json.dumps(dict(summary=row,chromosome=[dec.jobs[g] for g in best],schedule=raw),allow_nan=False))
            print(json.dumps(row),flush=True)
    manifest=dict(run_id=RUN_ID,input_path=str(source),input_sha256=digest,snapshot_sha256=snapshot_sha,
        decoder_module=V5ScheduleDecoder.__module__,objective='MIN_CMAX_FINAL_2C',seeds=args.seeds,
        calls_per_arm=args.budget,arms=ARMS,baseline_validation='PASS',
        protocol='Same saved incumbent per arm, strict Cmax acceptance, serial; new unique full search decodes including initial incumbent; reconstruction/validator reported separately. Not a full GA benchmark or equal wall-time protocol.',
        source_files_changed=[p for p,h in protected.items() if sha(root/p)!=h],
        input_unchanged=sha(source)==digest,snapshot_unchanged=sha(snapshot)==snapshot_sha)
    assert not manifest['source_files_changed'] and manifest['input_unchanged'] and manifest['snapshot_unchanged']
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2))
    (out/'traces.json').write_text(json.dumps(traces,allow_nan=False))
    with (out/'results.csv').open('x',encoding='utf-8-sig',newline='') as file:
        w=csv.DictWriter(file,fieldnames=list(results[0]));w.writeheader();w.writerows(results)
    return 0


if __name__=='__main__':raise SystemExit(main())
