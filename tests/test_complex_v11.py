"""Composite FAT scenarios plus independent analytic and randomized oracles."""
import copy,json,math,random,tempfile,unittest
from pathlib import Path
from esswave.comtrade import load
from esswave.engine import run
from esswave.generate import write_record
from esswave.complex_cases import CATALOG

ROOT=Path(__file__).resolve().parents[1]
MANIFEST=json.loads((ROOT/'examples/complex/manifest.json').read_text(encoding='utf-8'))

class CompositeScenarios(unittest.TestCase):pass
for entry in MANIFEST['cases']:
    def test(self,e=entry):
        result=run(load(ROOT/'examples/complex'/e['cfg']),json.loads((ROOT/'examples/complex'/e['case']).read_text(encoding='utf-8')))
        self.assertEqual(result['status'],e['expected'])
        actual={r['id']:r for r in result['results']};self.assertEqual(set(actual),set(e['expected_assertions']))
        for ident,expected in e['expected_assertions'].items():
            self.assertEqual(actual[ident]['status'],expected,ident+': '+actual[ident]['reason'])
        for ident,metrics in e['metric_oracles'].items():
            for metric,(lo,hi) in metrics.items():
                value=actual[ident]['metrics'][metric];self.assertGreaterEqual(value,lo);self.assertLessEqual(value,hi)
    setattr(CompositeScenarios,'test_'+entry['id'],test)

class AnalyticAndRandomizedOracles(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory()
    def tearDown(self):self.temp.cleanup()

for seed in range(24):
    def test(self,seed=seed):
        rng=random.Random(seed);dt=.0005;t=[i*dt for i in range(2401)];trigger=.2;delay=rng.uniform(.02,.06);tau=rng.uniform(.025,.085)
        baseline=rng.choice([0.,35.,100.]);target=rng.choice([-80.,0.,120.])
        if target==baseline:target+=100
        gain=rng.choice([.001,1.,1000.]);skew=rng.choice([-.00025,0,.00075])
        physical=[baseline if x+skew<trigger+delay else target+(baseline-target)*math.exp(-(x+skew-trigger-delay)/tau) for x in t]
        raw=[v/gain for v in physical]
        cfg=write_record(self.temp.name,'analytic',t,[{'name':'feedback','unit':'V','a':.0001,'skew_us':skew*1e6,'data':raw},
          {'name':'unused','unit':'V','data':[None]*len(t)}],[],fmt='FLOAT32')
        c={'schema_version':1,'name':'闭式解对照','channels':{'p':{'kind':'analog','name':'feedback','unit':'V','scale':gain}},'tests':[
          {'id':'STEP','type':'analog_step','channel':'p','trigger':{'type':'time','at':trigger},'window':[0,.9],
           'baseline_window':[-.12,-.02],'target':target,'normalization':'step','steady_last_s':.1,'settle_hold_s':.03,
           'limits':{'response_s_max':.4,'rise_s_max':.3,'overshoot_pct_max':.1}}]}
        r=run(load(cfg),c);self.assertEqual(r['status'],'PASS');m=r['results'][0]['metrics']
        # First-order response equations are independent of the analyzer's implementation.
        self.assertAlmostEqual(m['response_s'],delay+tau*math.log(10),delta=dt/2)
        self.assertAlmostEqual(m['rise_s'],tau*math.log(9),delta=dt/2)
        self.assertAlmostEqual(m['baseline'],baseline,delta=.0001)
    setattr(AnalyticAndRandomizedOracles,f'test_analytic_signed_step_seed_{seed:02d}',test)

for seed in range(24):
    def test(self,seed=seed):
        rng=random.Random(1000+seed);n=2001;active=seed%2;t=[i/1000 for i in range(n)]
        states=[1-active]*n;cursor=20;expected=[]
        for k in range(30):
            cursor+=rng.randrange(10,20);width=rng.randrange(5,26)
            for i in range(cursor,cursor+width):states[i]=active
            expected.append(width/1000);cursor+=width
        fmt=['ASCII','BINARY','BINARY32','FLOAT32'][seed%4]
        p=write_record(self.temp.name,'pulses',t,[],[{'name':'D','data':states}],fmt=fmt)
        c={'schema_version':1,'name':'随机脉冲列独立真值','channels':{'d':{'kind':'digital','name':'D'}},'tests':[
            {'id':'p','type':'digital_pulse','channel':'d','window':[0,2],'active':active,'count':30,'min_width_s':.005,'max_width_s':.025}]}
        r=run(load(p),c);self.assertEqual(r['status'],'PASS')
        actual=r['results'][0]['metrics']['widths_s'];self.assertEqual(len(actual),len(expected))
        for a,b in zip(actual,expected):self.assertAlmostEqual(a,b,10)
        # A single explicit width-limit mutation must change the verdict.
        c['tests'][0]['max_width_s']=min(expected)-.001;c['tests'][0]['min_width_s']=0
        self.assertEqual(run(load(p),c)['status'],'FAIL')
    setattr(AnalyticAndRandomizedOracles,f'test_random_low_high_pulse_train_seed_{seed:02d}',test)

if __name__=='__main__':unittest.main(verbosity=2)
