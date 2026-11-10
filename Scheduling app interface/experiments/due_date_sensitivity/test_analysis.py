"""Small arithmetic/mapping guards. Run python -B -m unittest discover here."""
import copy
import unittest
from analyze import completions, metrics, local


class SensitivityTests(unittest.TestCase):
    def test_exact_timestamp_and_both_averages(self):
        jobs=[dict(job_id='A',due_min=10.25),dict(job_id='B',due_min=30)]
        result=metrics(jobs,{'A':20.75,'B':29},1e-6)
        self.assertEqual(result['total_tardiness_min'],10.5)
        self.assertEqual(result['average_tardiness_all_jobs_min'],5.25)
        self.assertEqual(result['average_tardiness_tardy_jobs_min'],10.5)
        self.assertEqual(result['otd_percent'],50)

    def test_monotonic_same_cmax(self):
        jobs=[dict(job_id='A',due_min=0),dict(job_id='B',due_min=10)]
        completion={'A':2*1440+.25,'B':10+7*1440}
        rows=[metrics([{**j,'due_min':j['due_min']+days*1440} for j in jobs],completion,1e-6) for days in (0,1,3,5,7)]
        self.assertEqual([r['tardy_jobs'] for r in rows],[2,2,1,1,0])
        self.assertEqual(len({r['cmax_min'] for r in rows}),1)
        self.assertIsNone(rows[-1]['average_tardiness_tardy_jobs_min'])

    def test_timezone_offset_rejected(self):
        with self.assertRaises(ValueError):local('2026-10-01T06:00:00+07:00')

    def fixture(self):
        data=dict(metadata=dict(origin_datetime='2026-10-01 06:00:00',schema_version='AL13_BENCHMARK_V5'),
                  jobs=[dict(job_id=j,release_min=0,due_min=30) for j in ('A','B')])
        rows=[dict(eventType='2C_BATCH',eventId='one',jobId='A;B',jobIds=['A','B'],endMin=10,finishTime='2026-10-01 06:10:00'),
              dict(eventType='2C_BATCH',eventId='two',jobId='A',jobIds=['A'],endMin=20,finishTime='2026-10-01 06:20:00')]
        return data,dict(operations=rows),dict(job_ids=['A','B'])

    def test_pooled_and_multiple_final_batches_use_max(self):
        self.assertEqual(completions(*self.fixture()),{'A':20,'B':10})

    def test_missing_duplicate_unknown_jobs_or_events_rejected(self):
        for mutation in ('missing','duplicate_job','duplicate_event','unknown','missing_due','timestamp'):
            data,result,request=copy.deepcopy(self.fixture())
            if mutation=='missing':result['operations']=result['operations'][1:]
            elif mutation=='duplicate_job':data['jobs'].append(data['jobs'][0])
            elif mutation=='duplicate_event':result['operations'].append(result['operations'][0])
            elif mutation=='unknown':result['operations'][0]['jobIds']=['X','B']
            elif mutation=='missing_due':del data['jobs'][0]['due_min']
            else:result['operations'][0]['finishTime']='2026-10-01 06:10:01'
            with self.subTest(mutation=mutation),self.assertRaises((ValueError,KeyError)):
                completions(data,result,request)


if __name__=='__main__':unittest.main()
