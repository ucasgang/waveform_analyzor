"""Noise unit, counterexample, metamorphic and seeded holdout checks.
Seeds here are disjoint from the shipped 20261011 demonstration seed.
"""
from copy import deepcopy
from pathlib import Path
import json,math,random,tempfile,unittest
from esswave.comtrade import load
from esswave.engine import run,validate,ConfigError,InsufficientData
from esswave.generate import write_record
from esswave.noise_cases import CATALOG,EXPECTED,generate_case,policy,step,digital_policy
from esswave.robust import (condition,qualified_crossing,band_intervals,settling,weighted_quantile,
                             level_stats,digital_qualify,digital_audit,trigger_crossings)

class NoiseAlgorithms(unittest.TestCase):
    def test_hampel_replaces_isolated_spike_but_not_step(self):
        t=[i/1000 for i in range(101)];y=[0 if i<50 else 100 for i in range(101)];y[20]=25
        clean,a=condition(t,y,policy())
        self.assertEqual(clean[20],0);self.assertEqual(clean[49],0);self.assertEqual(clean[50],100)
        self.assertEqual(a['replaced_samples'],1)
    def test_negative_spike(self):
        t=[i/1000 for i in range(101)];y=[100.]*101;y[40]=-100
        clean,a=condition(t,y,policy());self.assertEqual(clean[40],100);self.assertEqual(a['replaced_samples'],1)
    def test_pulse_too_long_stays_raw(self):
        t=[i/1000 for i in range(101)];y=[125. if 40<=i<48 else 100. for i in range(101)]
        clean,a=condition(t,y,policy());self.assertEqual(max(clean),125.);self.assertEqual(a['replaced_samples'],0)
    def test_two_adjacent_spikes_and_audit(self):
        t=[i/1000 for i in range(101)];y=[100.]*101;y[40]=125;y[41]=50
        clean,a=condition(t,y,policy());self.assertEqual(clean[40:42],[100,100]);self.assertEqual(a['replaced_samples'],2)
        self.assertAlmostEqual(a['islands'][0]['duration_upper_s'],.003)
    def test_three_sample_spike_exceeds_configured_duration(self):
        t=[i/1000 for i in range(101)];y=[100.]*101;y[40:43]=[125]*3
        clean,a=condition(t,y,policy());self.assertEqual(a['replaced_samples'],0);self.assertEqual(max(clean),125)
    def test_endpoint_spike_not_silently_repaired(self):
        t=[i/1000 for i in range(101)];y=[100.]*101;y[-1]=125
        clean,a=condition(t,y,policy());self.assertEqual(clean[-1],125);self.assertEqual(a['replaced_samples'],0)
    def test_nan_not_imputed(self):
        with self.assertRaises(InsufficientData):condition([0,.001,.002],[1,float('nan'),1],policy())
    def test_low_rate_is_reported_as_unresolved_preprocessing(self):
        t=[i/100 for i in range(101)];y=[100.]*101
        clean,a=condition(t,y,policy())
        self.assertGreater(a['undersampled_windows'],0)
    def test_weighted_median_not_sample_count_biased(self):
        t=[0,.9]+[.91+i*.0001 for i in range(901)];y=[0,0]+[100]*(len(t)-2)
        self.assertEqual(weighted_quantile(t,y),0)
    def test_mad_stats_constant(self):
        d=level_stats([i/10 for i in range(11)],[7]*11)
        self.assertEqual(d['median'],7);self.assertEqual(d['sigma_mad'],0);self.assertEqual(d['drift'],0)
    def test_mad_stats_linear_drift(self):
        t=[i/100 for i in range(101)];d=level_stats(t,[10*x for x in t]);self.assertGreater(d['drift'],7)
    def test_crossing_rejects_short_early_spike(self):
        t=[i/1000 for i in range(101)];y=[1. if i==20 or i>=60 else 0. for i in range(101)]
        at,e=qualified_crossing(t,y,.9,.005,.02)
        self.assertAlmostEqual(at,.0599);self.assertEqual(e['rejected'],1);self.assertAlmostEqual(e['confirmed_s'],.0649)
    def test_crossing_pending_not_failure_to_measure(self):
        at,e=qualified_crossing([0,.1,.2],[0,0,1],.9,.02,0)
        self.assertIsNone(at);self.assertTrue(e['pending'])
    def test_crossing_hold_exact_at_downward_crossing(self):
        at,e=qualified_crossing([0,.1,.2,.3],[0,1,1,0],.5,.2,0)
        self.assertAlmostEqual(at,.05);self.assertAlmostEqual(e['confirmed_s'],.25)
    def test_hysteresis_allows_small_reentry(self):
        at,e=qualified_crossing([0,.1,.2,.3],[0,1,.49,1],.5,.2,.02);self.assertAlmostEqual(at,.05)
    def test_already_above_does_not_invent_latency(self):
        at,e=qualified_crossing([0,1],[1,1],.9,.01,0);self.assertIsNone(at);self.assertTrue(e['pre_responded'])
    def test_band_exact_durations(self):
        p=band_intervals([0,1,2],[0,2,0],1,.5)
        inside=sum(b-a for a,b,ok in p if ok);self.assertAlmostEqual(inside,1.)
    def test_settling_one_brief_dropout_allowed(self):
        t=[0,.4,.401,.402,1.];y=[100,100,90,100,100]
        at,e=settling(t,y,100,2,.1,.995,.002)
        self.assertEqual(at,0);self.assertLess(e['longest_excursion_s'],.002)
    def test_settling_long_dropout_not_diluted_by_long_tail(self):
        t=[0,.4,.5,.6,100.];y=[100,100,90,100,100]
        at,e=settling(t,y,100,2,.1,.99,.005);self.assertGreater(at,.5)
    def test_settling_strict_no_dropout(self):
        at,e=settling([0,1,2],[0,100,100],100,2,.1,1,0);self.assertAlmostEqual(at,.98)
    def test_digital_exact_minimum_width(self):
        r=digital_policy();r.update(rise_s=.002,fall_s=.001)
        f,e,p,j=digital_qualify([0,.001,.003,.004,.006],[0,1,0,0,0],r,.006)
        self.assertEqual(len(e),2);self.assertAlmostEqual(e[0]['time_s'],.001)
    def test_digital_unfinished_not_use_future(self):
        r=digital_policy();t=[i/1000 for i in range(21)];y=[int(i>=9) for i in range(21)]
        f,e,p,j=digital_qualify(t,y,r,.010)
        self.assertEqual(e,[]);self.assertEqual(p,[.009])
    def test_digital_audit_clips_edge_count_to_window(self):
        r=digital_policy();t=[i/1000 for i in range(11)];y=[int(2<=i<4) for i in range(11)]
        f,e,p,j=digital_qualify(t,y,r,.01)
        a=digital_audit(t,y,e,j,.003,.008,r)
        self.assertEqual(a['raw_edges'],1);self.assertEqual(a['rejected_edges'],1)
    def test_digital_chatter_audit(self):
        t=[i/1000 for i in range(100)];y=[i%2 if 20<=i<80 else 0 for i in range(100)];r=digital_policy()
        f,e,p,j=digital_qualify(t,y,r,.099);a=digital_audit(t,y,e,j,0,.099,r)
        self.assertGreater(a['longest_chatter_s'],.05);self.assertGreater(a['rejected_edges'],24)
    def test_trigger_hysteresis_requires_reset_witness(self):
        events,e=trigger_crossings([0,.1,.2,.3],[.5,.7,.5,.7],0,.3,.5,1,.15,0)
        self.assertEqual(events,[])
    def test_trigger_persistence_rejects_false_event(self):
        t=[i/1000 for i in range(101)];y=[int(i==20 or i>=60) for i in range(101)]
        events,e=trigger_crossings(t,y,0,.1,.5,1,.1,.005)
        self.assertEqual(len(events),1);self.assertGreater(events[0]['time_s'],.059);self.assertEqual(e['rejected_candidates'],1)
    def test_trigger_falling_symmetric(self):
        t=[0,.01,.02,.03,.04];y=[1,1,0,0,0]
        events,e=trigger_crossings(t,y,0,.04,.5,-1,.1,.005)
        self.assertEqual(len(events),1);self.assertAlmostEqual(events[0]['time_s'],.016)
    def test_trigger_cannot_confirm_after_cutoff(self):
        t=[0,.01,.02,.03,.04];y=[0,0,1,1,1]
        events,e=trigger_crossings(t,y,0,.021,.5,1,.1,.01)
        self.assertEqual(events,[]);self.assertTrue(e['pending'])
    def test_bounded_window_computation(self):
        r=policy();r['hampel_window_s']=10;r['max_spike_s']=.1
        with self.assertRaises(InsufficientData):condition([i/1000 for i in range(5001)],[0.]*5001,r)

class NoiseEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.temp=tempfile.TemporaryDirectory();cls.folder=Path(cls.temp.name)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def run_case(self,k,fmt='FLOAT32',seed=20261011):
        cfg,cp,entry=generate_case(self.folder,k,fmt,seed);c=json.loads(cp.read_text(encoding='utf-8'))
        return load(cfg),c,entry
    def test_raw_hard_limit_not_removed(self):
        record,c,_=self.run_case('N11_raw_safety_spike');r=run(record,c)['results'][0]
        self.assertEqual(r['status'],'FAIL');self.assertGreater(r['metrics']['replaced_samples'],0)
        self.assertEqual(next(v['status'] for v in r['checks'] if v['metric']=='raw_maximum'),'FAIL')
    def test_ambiguous_peak_never_autopasses(self):
        record,c,_=self.run_case('N12_ambiguous_spike');r=run(record,c)['results'][0]
        self.assertEqual(r['status'],'INCONCLUSIVE');self.assertGreater(r['metrics']['raw_overshoot_pct'],20)
        self.assertLess(r['metrics']['overshoot_pct'],5)
    def test_no_silent_gap_repair(self):
        record,c,_=self.run_case('N25_noise_with_gap');r=run(record,c)['results'][0]
        self.assertEqual(r['status'],'INCONCLUSIVE');self.assertIn('间隙',r['reason'])
    def test_quality_fail_invalidates_performance_not_raw_guard(self):
        record,c,_=self.run_case('N06_burst_noise');r=run(record,c)['results'][0]
        self.assertTrue(all(v['status']=='INCONCLUSIVE' for v in r['checks'] if v['metric'] in ('response_s','overshoot_pct','steady_error_pct')))
    def test_asymmetric_digital_reports_suppressed_evidence(self):
        record,c,_=self.run_case('N15_contact_bounce');r=run(record,c)['results'][0]
        self.assertGreater(r['evidence']['digital_noise']['rejected_edges'],0);self.assertIn('processed_trace',r['evidence'])
    def test_repair_budget_is_not_auto_increased(self):
        record,c,_=self.run_case('N02_emi_impulses');c['tests'][0]['robust']['max_replaced_fraction']=0
        r=run(record,c)['results'][0];self.assertEqual(r['status'],'INCONCLUSIVE')
        self.assertEqual(next(v['status'] for v in r['checks'] if v['metric']=='repair_fraction'),'INCONCLUSIVE')
    def test_raw_record_is_not_mutated(self):
        record,c,_=self.run_case('N02_emi_impulses');original=record.values['A1'].tobytes()
        r1=run(record,c);r2=run(record,c)
        self.assertEqual(original,record.values['A1'].tobytes());self.assertEqual(r1['results'],r2['results'])
    def test_noise_legacy_compatibility_is_explicit(self):
        record,c,_=self.run_case('N01_white_noise');plain=deepcopy(c);plain['tests'][0].pop('robust')
        r=run(record,c)['results'][0];legacy=run(record,plain)['results'][0]
        self.assertEqual(r['evidence']['raw_metrics'],legacy['metrics']);self.assertEqual(r['evidence']['raw_checks'],legacy['checks'])
    def test_positive_scale_offset_invariance(self):
        record,c,_=self.run_case('N01_white_noise');original=run(record,c)['results'][0]
        scale=2.3;offset=35.;c=deepcopy(c);c['channels']['p'].update(scale=scale,offset=offset)
        a=c['tests'][0];a['target']=a['target']*scale+offset
        for k in ('hampel_floor','max_noise_sigma','max_baseline_drift','max_steady_drift'):a['robust'][k]*=scale
        for k in ('raw_min','raw_max'):a['robust'][k]=a['robust'][k]*scale+offset
        a['limits']['steady_ripple_pp_max']*=scale
        other=run(record,c)['results'][0]
        self.assertEqual(other['status'],original['status'])
        for k in ('response_s','rise_s','overshoot_pct','steady_error_pct'):self.assertAlmostEqual(other['metrics'][k],original['metrics'][k],places=8)
    def test_processed_amplitude_requires_guard_near_limit(self):
        record,c,_=self.run_case('N01_white_noise');a=c['tests'][0];a['robust']['metric_guard_pct']=.2
        r=run(record,c)['results'][0]
        a['limits']['overshoot_pct_max']=r['metrics']['overshoot_pct']+.05
        r=run(record,c)['results'][0]
        self.assertEqual(next(x['status'] for x in r['checks'] if x['metric']=='overshoot_pct'),'INCONCLUSIVE')

for _k in CATALOG:
    def fixture(self,k=_k):
        record,c,e=self.run_case(k)
        if e['expected']=='ERROR':
            with self.assertRaises(ConfigError):run(record,c)
            return
        r=run(record,c)
        self.assertEqual(r['status'],e['expected']);self.assertEqual({x['id']:x['status'] for x in r['results']},e['expected_assertions'])
        for aid,oracle in e['metric_oracles'].items():
            m=next(x['metrics'] for x in r['results'] if x['id']==aid)
            for key,(lo,hi) in oracle.items():self.assertTrue(lo<=m[key]<=hi,(k,key,m[key],lo,hi))
    setattr(NoiseEndToEnd,'test_fixture_'+_k,fixture)

for _fmt in ('ASCII','BINARY','BINARY32','FLOAT32'):
    for _k in ('N01_white_noise','N02_emi_impulses','N11_raw_safety_spike','N15_contact_bounce','N21_noisy_analog_trigger','N23_multirate_noise'):
        def encoding(self,k=_k,fmt=_fmt):
            record,c,e=self.run_case(k,fmt);self.assertEqual(run(record,c)['status'],e['expected'])
        setattr(NoiseEndToEnd,'test_encoding_'+_fmt+'_'+_k,encoding)

class NoiseValidation(unittest.TestCase):
    def base(self):return {'schema_version':1,'name':'validation','channels':{'p':{'kind':'analog','index':1}},'tests':[step()]}
    def test_required_raw_limits(self):
        c=self.base();del c['tests'][0]['robust']['raw_max']
        with self.assertRaises(ConfigError):validate(c)
    def test_long_island_repair_prohibited(self):
        c=self.base();c['tests'][0]['robust']['max_spike_s']=.1
        with self.assertRaises(ConfigError):validate(c)
    def test_bad_hysteresis(self):
        c=self.base();c['tests'][0]['robust']['crossing_hysteresis_fraction']=.1
        with self.assertRaises(ConfigError):validate(c)
    def test_invalid_clip_triplet(self):
        c=self.base();c['tests'][0]['robust']['clip_max']=100
        with self.assertRaises(ConfigError):validate(c)
    def test_unknown_parameter_fails_closed(self):
        c=self.base();c['tests'][0]['robust']['auto_tune_until_pass']=True
        with self.assertRaises(ConfigError):validate(c)
    def test_repair_10_percent_cap(self):
        c=self.base();c['tests'][0]['robust']['max_replaced_fraction']=.11
        with self.assertRaises(ConfigError):validate(c)

for _key in ('raw_min','raw_max','hampel_window_s','crossing_hold_s','max_noise_sigma'):
    for _value,_label in ((True,'bool'),(float('nan'),'nan'),(float('inf'),'inf'),('1','str')):
        def invalid(self,k=_key,v=_value):
            c=self.base();c['tests'][0]['robust'][k]=v
            with self.assertRaises(ConfigError):validate(c)
        setattr(NoiseValidation,'test_invalid_'+_key+'_'+_label,invalid)

class SeededNoiseHoldout(unittest.TestCase):
    """192 independently generated measurements, not 192 hard-coded statuses."""
    def trial(self,seed,mode):
        rng=random.Random(seed);t=[i/1000 for i in range(2001)];sign=rng.choice((-1,1));sigma=rng.uniform(.08,.4)
        ideal=lambda x:0. if x<.28 else 100*(1-math.exp(-(x-.28)/.075))
        y=[sign*(ideal(x)+max(-.8,min(.8,rng.gauss(0,sigma)))) for x in t]
        onset=rng.randint(650,1100);expected='PASS'
        if mode=='fault':
            duration=rng.randint(20,100)
            for i in range(onset,onset+duration):y[i]=sign*(125+rng.uniform(-.5,.5))
            expected='FAIL'
        elif mode=='ambiguous':y[onset]=sign*125.;expected='INCONCLUSIVE'
        a=step(sign*100);a['robust'].update(raw_min=-160,raw_max=160)
        c={'schema_version':1,'name':f'holdout {seed} {mode}','channels':{'p':{'kind':'analog','index':1}},'tests':[a]}
        with tempfile.TemporaryDirectory() as d:
            cfg=write_record(d,'trial',t,[{'name':'P','unit':'kW','a':.001,'data':y}],[],fmt='FLOAT32')
            r=run(load(cfg),c)['results'][0]
        self.assertEqual(r['status'],expected,{'seed':seed,'mode':mode,'reason':r['reason'],'metrics':r['metrics']})
        if mode=='healthy':
            self.assertAlmostEqual(r['metrics']['response_s'],.03+.075*math.log(10),delta=.013)
            self.assertAlmostEqual(r['metrics']['rise_s'],.075*math.log(9),delta=.016)
        if mode=='fault':self.assertGreater(r['metrics']['overshoot_pct'],20.)
        if mode=='ambiguous':self.assertIn('raw_peak_review',[x['metric'] for x in r['checks']])
for _seed in range(14000,14064):
    for _mode in ('healthy','fault','ambiguous'):
        def trial(self,seed=_seed,mode=_mode):self.trial(seed,mode)
        setattr(SeededNoiseHoldout,f'test_{_seed}_{_mode}',trial)
