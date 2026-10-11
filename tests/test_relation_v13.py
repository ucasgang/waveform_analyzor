import copy,json,math,tempfile,unittest
from pathlib import Path
from esswave.comtrade import load
from esswave.generate import write_record
from esswave.engine import run,validate
from esswave.relation import outside_duration

class RelationTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory()
    def tearDown(self):self.tmp.cleanup()
    def make(self,ys=None,rs=None,skew=0,t=None):
        t=t or [i/100 for i in range(101)];ys=ys if ys is not None else [2*x for x in t];rs=rs if rs is not None else [x for x in t]
        record=load(write_record(self.tmp.name,'relation',t,[{'name':'P','unit':'kW','a':1,'data':ys},{'name':'R','unit':'kW','a':1,'skew_us':skew,'data':rs}],[],fmt='FLOAT32'))
        c={'schema_version':1,'name':'relation','channels':{'p':{'kind':'analog','name':'P','unit':'kW'},'r':{'kind':'analog','name':'R','unit':'kW'}},
          'tests':[{'id':'sum','type':'analog_relation','channel':'p','window':[.1,.9],'references':[{'channel':'r','gain':2}],'tolerance':.001,'max_violation_s':0,'hard_max_error':.002}]}
        return record,c
    def test_linear_gain_pass(self):r,c=self.make();self.assertEqual(run(r,c)['status'],'PASS')
    def test_constant_bias_failure(self):
        t=[i/100 for i in range(101)];r,c=self.make(ys=[2*x+1 for x in t]);a=run(r,c)['results'][0];self.assertEqual(a['status'],'FAIL');self.assertAlmostEqual(a['metrics']['relation_violation_s'],.8)
    def test_explicit_offset(self):
        t=[i/100 for i in range(101)];r,c=self.make(ys=[2*x+1 for x in t]);c['tests'][0]['offset']=1;self.assertEqual(run(r,c)['status'],'PASS')
    def test_triangle_duration_independent_golden(self):
        d,first=outside_duration([0,1,2],[0,2,0],1);self.assertAlmostEqual(d,1);self.assertAlmostEqual(first,.5)
    def test_nonuniform_triangle_duration(self):
        d,_=outside_duration([0,.25,2],[0,2,0],1);self.assertAlmostEqual(d,1)
    def test_signed_crossing_duration(self):
        d,first=outside_duration([0,1],[-2,2],1);self.assertAlmostEqual(d,.5);self.assertEqual(first,0)
    def test_touch_boundary_no_violation(self):self.assertEqual(outside_duration([0,1,2],[0,1,0],1)[0],0)
    def test_zero_tolerance_exact_equality(self):self.assertEqual(outside_duration([0,1],[0,0],0)[0],0)
    def test_skewed_linear_reference_aligned(self):
        t=[i/100 for i in range(101)];r,c=self.make(t=t,rs=[x+.002 for x in t],skew=2000);self.assertEqual(run(r,c)['status'],'PASS')
    def test_reference_only_peak_on_union_grid(self):
        r,c=self.make(ys=[0,0,0],rs=[0,1,0],t=[0,.01,.02],skew=5000);a=c['tests'][0];a.update(window=[.005,.02],references=[{'channel':'r','gain':1}],tolerance=.75,max_violation_s=.004,hard_max_error=.9)
        out=run(r,c)['results'][0];self.assertEqual(out['status'],'FAIL');self.assertAlmostEqual(out['metrics']['relation_max_error'],1);self.assertAlmostEqual(out['metrics']['relation_violation_s'],.005)
    def test_missing_reference_not_ignored(self):
        r,c=self.make();r.values['A2'][50]=math.nan;self.assertEqual(run(r,c)['status'],'INCONCLUSIVE')
    def test_gap_before_window_does_not_taint_window(self):
        r,c=self.make();r.gaps=[(0,.05)];self.assertEqual(run(r,c)['status'],'PASS')
    def test_gap_inside_window_is_inconclusive(self):
        r,c=self.make();r.gaps=[(.4,.5)];self.assertEqual(run(r,c)['status'],'INCONCLUSIVE')
    def test_units_must_be_explicit(self):
        r,c=self.make();c['channels']['r'].update(output_unit='W',scale=1000);self.assertEqual(run(r,c)['status'],'ERROR')
    def test_self_alias_rejected(self):
        r,c=self.make();c['tests'][0]['references'][0]['channel']='p'
        with self.assertRaises(ValueError):validate(c)
    def test_alias_maps_to_same_raw_channel_rejected(self):
        r,c=self.make();c['channels']['r']['name']='P';self.assertEqual(run(r,c)['status'],'ERROR')
    def test_duplicate_references_rejected(self):
        r,c=self.make();c['tests'][0]['references']*=2
        with self.assertRaises(ValueError):validate(c)
    def test_uncertainty_must_not_force_pass_or_fail(self):
        r,c=self.make(ys=[.9]*101,rs=[0.]*101);c['tests'][0].update(tolerance=1,hard_max_error=1,alignment_error_bound=.2)
        self.assertEqual(run(r,c)['status'],'INCONCLUSIVE')
    def test_bound_does_not_hide_large_fault(self):
        r,c=self.make(ys=[5.]*101,rs=[0.]*101);c['tests'][0].update(tolerance=1,hard_max_error=1,alignment_error_bound=.2)
        self.assertEqual(run(r,c)['status'],'FAIL')
    def test_small_bound_can_prove_pass(self):
        r,c=self.make();c['tests'][0].update(tolerance=1,hard_max_error=1,alignment_error_bound=.1);self.assertEqual(run(r,c)['status'],'PASS')
    def test_inputs_immutable(self):
        r,c=self.make();b=bytes(r.values['A1']);snapshot=copy.deepcopy(c);run(r,c);self.assertEqual(bytes(r.values['A1']),b);self.assertEqual(c,snapshot)
    def test_invalid_alignment_budget(self):
        r,c=self.make();c['tests'][0]['alignment_error_bound']=-1
        with self.assertRaises(ValueError):validate(c)
    def test_nonfinite_gain(self):
        r,c=self.make();c['tests'][0]['references'][0]['gain']=math.inf
        with self.assertRaises(ValueError):validate(c)

class TriggerPrefixTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory()
    def tearDown(self):self.tmp.cleanup()
    def make(self):
        t=[i*.001 for i in range(1001)];d=[int(.03<=x<.10 or x>=.7) for x in t]
        rec=load(write_record(self.tmp.name,'trigger',t,[{'name':'A','unit':'V','a':1,'data':t}],[{'name':'D','data':d}],fmt='FLOAT32'))
        cfg={'schema_version':1,'name':'prefix','channels':{'a':{'kind':'analog','name':'A'},'d':{'kind':'digital','name':'D'}},'tests':[{'id':'x','type':'analog_range','channel':'a','window':[0,.05],'min':0,'max':1,'trigger':{'type':'digital_edge','channel':'d','edge':'rising'}}]}
        return rec,cfg
    def test_later_gap_no_longer_invalidates_earlier_trigger(self):
        r,c=self.make();r.gaps=[(.6,.62)];a=run(r,c)['results'][0];self.assertEqual(a['status'],'PASS');self.assertLess(a['evidence']['trigger']['validated_prefix_end_s'],.6)
    def test_gap_before_occurrence_still_rejected(self):
        r,c=self.make();r.gaps=[(.02,.025)];self.assertEqual(run(r,c)['status'],'INCONCLUSIVE')
    def test_second_occurrence_cannot_skip_missing_history(self):
        r,c=self.make();r.gaps=[(.6,.62)];c['tests'][0]['trigger']['occurrence']=2;self.assertEqual(run(r,c)['status'],'INCONCLUSIVE')
    def test_event_confirmation_crossing_gap_rejected(self):
        r,c=self.make();r.gaps=[(.031,.04)];c['tests'][0]['trigger']['debounce_s']=.02;self.assertEqual(run(r,c)['status'],'INCONCLUSIVE')
    def test_future_trigger_chatter_not_used_to_reject_prefix(self):
        r,c=self.make()
        for i in range(600,680):r.values['D1'][i]=i%2
        c['tests'][0]['trigger']['noise']={'rise_s':.002,'fall_s':.002,'max_rejected_edges':2,'max_chatter_s':.005}
        self.assertEqual(run(r,c)['status'],'PASS')
    def test_append_future_missing_analog_does_not_change_trigger(self):
        r,c=self.make();c['tests'][0]['trigger']={'type':'analog_crossing','channel':'a','threshold':.03,'edge':'rising'}
        r.values['A1'][900]=math.nan;self.assertEqual(run(r,c)['status'],'PASS')
