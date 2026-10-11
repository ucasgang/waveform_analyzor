"""Independent boundary regressions. These tests fail against the original 1.0 engine.
Expected results follow observation intervals and hand-defined signals, not the generator's labels.
"""
from array import array
import copy, math, tempfile, unittest
from pathlib import Path
from esswave.comtrade import load, ComtradeError
from esswave.engine import run, compact_trace
from esswave.generate import write_record


def suite(a):
    return {'schema_version':1,'name':'边界回归','channels':{
        'a':{'kind':'analog','name':'A','unit':'V'},'d':{'kind':'digital','name':'D'}},'tests':[a]}

def step():
    return {'id':'a','type':'analog_step','channel':'a','trigger':{'type':'time','at':.2},
            'window':[0,1],'baseline_window':[-.1,-.01],'target':100,'normalization':'rated','rated':100,
            'steady_last_s':.1,'limits':{'rise_s_max':.2}}

class BoundaryRegressions(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def rec(self,t,y=None,d=None,**kwargs):
        p=write_record(self.root,'boundary',t,[{'name':'A','unit':'V','a':.01,'data':y if y is not None else [1]*len(t)}],
                       [{'name':'D','data':d if d is not None else [0]*len(t)}],fmt='FLOAT32',**kwargs)
        return load(p)
    def test_B01_late_low_crossing_does_not_prove_rise_failure(self):
        t=[i/1000 for i in range(1201)];y=[max(0,min(50,(x-1.14)*1000)) for x in t]
        r=run(self.rec(t,y),suite(step()));self.assertEqual(r['status'],'INCONCLUSIVE')
        self.assertAlmostEqual(r['results'][0]['metrics']['latency_s'],.95,6)
    def test_B01_missing_low_crossing_is_not_a_rise_failure(self):
        t=[i/1000 for i in range(1201)]
        self.assertEqual(run(self.rec(t,[0]*len(t)),suite(step()))['status'],'INCONCLUSIVE')
    def test_B01_enough_time_since_low_proves_rise_failure(self):
        t=[i/1000 for i in range(1201)];y=[max(0,min(50,(x-.5)*1000)) for x in t]
        self.assertEqual(run(self.rec(t,y),suite(step()))['status'],'FAIL')
    def test_B01_response_limit_still_fails_without_any_motion(self):
        t=[i/1000 for i in range(1201)];a=step();a['limits']={'response_s_max':.2}
        self.assertEqual(run(self.rec(t,[0]*len(t)),suite(a))['status'],'FAIL')
    def test_B02_no_future_information_for_debounce_at_window_end(self):
        t=[i/1000 for i in range(201)];d=[int(.098<=x<.102) for x in t]
        a={'id':'d','type':'digital_hold','channel':'d','window':[0,.1],'value':0,'debounce_s':.005}
        self.assertEqual(run(self.rec(t,d=d),suite(a))['status'],'INCONCLUSIVE')
    def test_B02_extending_record_cannot_change_a_closed_window(self):
        a={'id':'d','type':'digital_hold','channel':'d','window':[0,.1],'value':0,'debounce_s':.005}
        states=[]
        for n in (101,103,151):
            t=[i/1000 for i in range(n)];d=[int(.098<=x<.102) for x in t]
            states.append(run(self.rec(t,d=d),suite(a))['status'])
        self.assertEqual(states,['INCONCLUSIVE']*3)
    def test_B02_glitch_fully_observed_inside_window_can_be_filtered(self):
        t=[i/1000 for i in range(201)];d=[int(.05<=x<.052) for x in t]
        a={'id':'d','type':'digital_hold','channel':'d','window':[0,.1],'value':0,'debounce_s':.005}
        self.assertEqual(run(self.rec(t,d=d),suite(a))['status'],'PASS')
    def test_B03_crossing_at_inclusive_left_search_boundary(self):
        t=[i/1000 for i in range(201)];a={'id':'a','type':'analog_range','channel':'a','window':[0,.05],
            'min':0,'max':100,'trigger':{'type':'analog_crossing','channel':'a','threshold':10,'search':[.1,.15]}}
        r=run(self.rec(t,[x*100 for x in t]),suite(a));self.assertEqual(r['status'],'PASS')
        self.assertAlmostEqual(r['results'][0]['evidence']['trigger']['at'],.1,8)
    def test_B03_no_invented_crossing_for_threshold_plateau(self):
        t=[i/1000 for i in range(201)];a={'id':'a','type':'analog_range','channel':'a','window':[0,.05],
            'min':0,'max':100,'trigger':{'type':'analog_crossing','channel':'a','threshold':10,'search':[.12,.15]}}
        self.assertEqual(run(self.rec(t,[min(x*100,10) for x in t]),suite(a))['status'],'INCONCLUSIVE')
    def test_B04_trigger_predecessor_in_gap_is_not_usable_evidence(self):
        t=[i/1000 for i in range(101)]+[.2+i/1000 for i in range(101)]
        a={'id':'d','type':'digital_hold','channel':'d','window':[0,.05],'value':1,
           'trigger':{'type':'digital_edge','channel':'d','search':[.2,.25]}}
        self.assertEqual(run(self.rec(t,d=[int(x>=.2) for x in t]),suite(a))['status'],'INCONCLUSIVE')
    def test_B04_asserted_edge_cannot_hide_gap_just_outside_window(self):
        t=[i/1000 for i in range(101)]+[.2+i/1000 for i in range(101)]
        a={'id':'d','type':'digital_edge','channel':'d','window':[.2,.25],'edge':'rising','count':1}
        self.assertEqual(run(self.rec(t,d=[int(x>=.2) for x in t]),suite(a))['status'],'INCONCLUSIVE')
    def test_B04_unrelated_earlier_gap_does_not_poison_stable_hold(self):
        t=[i/1000 for i in range(101)]+[.2+i/1000 for i in range(101)]
        a={'id':'d','type':'digital_hold','channel':'d','window':[.25,.3],'value':1}
        self.assertEqual(run(self.rec(t,d=[int(x>=.2) for x in t]),suite(a))['status'],'PASS')
    def test_B05_time_multiplier_overflow_rejected(self):
        self.rec([0,.001,.002]);p=self.root/'boundary.cfg';lines=p.read_text().splitlines();lines[-3]='1e308';p.write_text('\n'.join(lines)+'\n')
        with self.assertRaises(ComtradeError):load(p)
    def test_B05_engineering_scale_overflow_rejected(self):
        self.rec([0,.001,.002],[1,2,3]);p=self.root/'boundary.cfg';lines=p.read_text().splitlines();row=lines[2].split(',');row[5]='1e308';lines[2]=','.join(row);p.write_text('\n'.join(lines)+'\n')
        with self.assertRaises(ComtradeError):load(p)
    def test_B06_compact_trace_keeps_missing_as_breaks(self):
        t=[i/1000 for i in range(101)];y=[1. if i<50 else (math.nan if i<60 else 2.) for i in range(101)]
        trace=compact_trace(t,y)
        self.assertTrue(any(p[1] is None for p in trace))
    def test_B06_dense_missing_sample_cannot_be_bridged(self):
        t=[i/1000 for i in range(10001)];y=[float(i) for i in range(len(t))];y[4888]=math.nan
        trace=compact_trace(t,y,limit=100)
        self.assertTrue(any(p[1] is None for p in trace));self.assertLessEqual(len(trace),100)

    def test_B05_mapping_overflow_is_error_not_missing_data(self):
        r=self.rec([0,.001,.002],[1,2,3]);c=suite({'id':'a','type':'analog_range','channel':'a','window':[0,.002],'min':0})
        c['channels']['a']['scale']=1e308
        self.assertEqual(run(r,c)['status'],'ERROR')
    def test_B05_intermediate_nonfinite_mean_never_reports_pass(self):
        r=self.rec([0,.001,.002]);r.values['A1']=array('d',[1e308]*3)
        c=suite({'id':'a','type':'analog_range','channel':'a','window':[0,.002],'min':0})
        result=run(r,c);self.assertEqual(result['status'],'ERROR')
        import json
        json.dumps(result,allow_nan=False)

if __name__=='__main__':unittest.main(verbosity=2)
