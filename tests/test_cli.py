"""In-process CLI tests complement actual subprocess exit-code checks."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from esswave.cli import main,batch,read_config
ROOT=Path(__file__).resolve().parents[1]
class CliTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name);self.sink=io.StringIO()
    def tearDown(self):self.temp.cleanup()
    def call(self,args):
        with contextlib.redirect_stdout(self.sink),contextlib.redirect_stderr(self.sink):return main(args)
    def test_inspect(self):self.assertEqual(self.call(['inspect',str(ROOT/'examples/01_pass_binary.cfg')]),0)
    def test_run_pass(self):self.assertEqual(self.call(['run','--cfg',str(ROOT/'examples/01_pass_binary.cfg'),'--case',str(ROOT/'examples/01_pass_binary.json'),'--out',str(self.folder/'out')]),0)
    def test_run_fail(self):self.assertEqual(self.call(['run','--cfg',str(ROOT/'examples/06_overshoot.cfg'),'--case',str(ROOT/'examples/06_overshoot.json'),'--out',str(self.folder/'out')]),1)
    def test_run_inconclusive(self):self.assertEqual(self.call(['run','--cfg',str(ROOT/'examples/10_no_trigger.cfg'),'--case',str(ROOT/'examples/10_no_trigger.json'),'--out',str(self.folder/'out')]),3)
    def test_error(self):self.assertEqual(self.call(['inspect',str(self.folder/'missing.cfg')]),2)
    def test_generate(self):self.assertEqual(self.call(['generate','--out',str(self.folder/'examples')]),0);self.assertEqual(len(json.loads((self.folder/'examples/manifest.json').read_text())['cases']),23)
    def manifest(self,entries):
        p=self.folder/'manifest.json';p.write_text(json.dumps({'cases':entries}));return p
    def entry(self,name,expected=None):
        c={'cfg':str(ROOT/'examples'/f'{name}.cfg'),'case':str(ROOT/'examples'/f'{name}.json')}
        if expected is not None:c['expected']=expected
        return c
    def test_expected_outcome_regression_not_acceptance(self):
        m=self.manifest([self.entry('01_pass_binary','PASS'),self.entry('06_overshoot','FAIL')]);self.assertEqual(self.call(['batch','--manifest',str(m),'--out',str(self.folder/'out')]),0)
    def test_mismatched_regression(self):
        m=self.manifest([self.entry('01_pass_binary','FAIL')]);self.assertEqual(self.call(['batch','--manifest',str(m),'--out',str(self.folder/'out')]),1)
    def test_acceptance_batch_fails(self):
        m=self.manifest([self.entry('06_overshoot')]);self.assertEqual(self.call(['batch','--manifest',str(m),'--out',str(self.folder/'out')]),1)
    def test_missing_file_is_logged(self):
        m=self.manifest([{'cfg':'missing.cfg','case':'missing.json'}]);self.assertEqual(self.call(['batch','--manifest',str(m),'--out',str(self.folder/'out')]),2);self.assertTrue((self.folder/'out/001_missing/error.json').exists())
    def test_empty_manifest(self):
        m=self.manifest([]);self.assertEqual(self.call(['batch','--manifest',str(m),'--out',str(self.folder/'out')]),2)
    def test_directory_without_case(self):self.assertEqual(self.call(['batch','--directory',str(self.folder)]),2)
    def test_directory_without_cfg(self):self.assertEqual(self.call(['batch','--directory',str(self.folder),'--case',str(ROOT/'examples/01_pass_binary.json')]),2)
    def test_default_gui_calls_local_server(self):
        with patch('esswave.server.serve') as s:self.assertEqual(self.call([]),0);s.assert_called_once_with(8765,True)
    def test_gui_port_and_no_browser(self):
        with patch('esswave.server.serve') as s:self.assertEqual(self.call(['gui','--port','9999','--no-browser']),0);s.assert_called_once_with(9999,False)
    def test_config_rejects_nan_json(self):
        p=self.folder/'bad.json';p.write_text('{"x": NaN}')
        with self.assertRaises(ValueError):read_config(p)
    def test_config_bom(self):
        p=self.folder/'bom.json';p.write_text('\ufeff{"x":1}',encoding='utf-8');self.assertEqual(read_config(p),{'x':1})
    def test_version(self):
        with self.assertRaises(SystemExit) as x:self.call(['--version'])
        self.assertEqual(x.exception.code,0)
