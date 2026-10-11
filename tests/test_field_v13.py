"""Saved fault-injection expectations, independent seed runs and physical invariants.
Truth sidecars are exclusively a test oracle, never inputs to the engine.
"""
import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from esswave.field_cases import CATALOG,generate_case
from esswave.comtrade import load
from esswave.engine import run
ROOT=Path(__file__).resolve().parents[1]
class FieldSceneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries=json.loads((ROOT/'examples/field/manifest.json').read_text())['cases'];cls.entries={Path(e['cfg']).stem:e for e in cls.entries}
    def verify(self,ident):
        e=self.entries[ident];p=ROOT/'examples/field'/e['cfg'];c=json.loads((ROOT/'examples/field'/e['case']).read_text());r=run(load(p),c)
        self.assertEqual(r['status'],e['expected']);self.assertEqual({a['id']:a['status'] for a in r['results']},e['expected_tests'])
for ident in CATALOG:
    setattr(FieldSceneTests,'test_'+ident,lambda self,ident=ident:self.verify(ident))

class FieldSeedTests(unittest.TestCase):
    def verify(self,ident,seed):
        with tempfile.TemporaryDirectory() as d:
            cfg,case,truth=generate_case(d,ident,seed=seed);r=run(load(cfg),json.loads(case.read_text()))
            self.assertEqual(r['status'],truth['expected']);self.assertEqual({a['id']:a['status'] for a in r['results']},truth['expected_tests'])
            self.assertLess(truth['independent_invariants']['maximum_discrete_dc_energy_balance_error_J'],1e-8)
            latent=truth['latent_preview']
            self.assertTrue(all(abs(v[1]+v[2]-v[3])<1e-9 for v in latent))
            self.assertTrue(all(v[4]>0 and 0<v[6]<1 for v in latent))
            self.assertTrue(all(v[7]<=160+1e-9 for v in latent))
for ident in ('F01_coupled_nominal','F05_trip_1ms','F08_common_mode_emi'):
    for seed in (1,17,90210,65537):
        setattr(FieldSeedTests,'test_'+ident+'_seed_'+str(seed),lambda self,ident=ident,seed=seed:self.verify(ident,seed))

class FieldTruthTests(unittest.TestCase):
    def test_saved_files_hashes_match_all_injections(self):
        for p in (ROOT/'examples/field').rglob('*.truth.json'):
            t=json.loads(p.read_text());base=p.with_name(p.name.replace('.truth.json',''))
            self.assertEqual(hashlib.sha256(base.with_suffix('.cfg').read_bytes()).hexdigest(),t['cfg_sha256']);self.assertEqual(hashlib.sha256(base.with_suffix('.dat').read_bytes()).hexdigest(),t['dat_sha256'])
    def test_generator_repeatability_and_seed_diversity(self):
        with tempfile.TemporaryDirectory() as d:
            a=generate_case(Path(d)/'a','F09_clock_jitter',seed=314)[2];b=generate_case(Path(d)/'b','F09_clock_jitter',seed=314)[2];c=generate_case(Path(d)/'c','F09_clock_jitter',seed=271)[2]
            self.assertEqual(a['dat_sha256'],b['dat_sha256']);self.assertNotEqual(a['dat_sha256'],c['dat_sha256'])
    def test_fault_outcome_not_read_from_sidecar(self):
        with tempfile.TemporaryDirectory() as d:
            cfg,case,truth=generate_case(d,'F05_trip_1ms');cfg.with_name(cfg.stem+'.truth.json').write_text('{"expected":"PASS"}')
            c=json.loads(case.read_text());c['tests']=[a for a in c['tests'] if a['id']=='NO_TRIP'];self.assertEqual(run(load(cfg),c)['status'],'FAIL')
    def test_unsafe_undersampling_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):generate_case(d,'F01_coupled_nominal',fs=500)
