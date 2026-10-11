"""Real HTTP verification of processed overlays, cache identity and exports."""
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import json,threading,unittest
from esswave.server import State,make_handler
from esswave.noise_view import processed_record
from esswave.trace import trace_window

class NoiseHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state=State();h=make_handler(cls.state);h.log_message=lambda *a:None
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),h);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.state.close()
    def request(self,path,body=None):
        data=None if body is None else json.dumps(body).encode();req=Request(self.url+path,data=data,headers={'X-ESS-Token':self.state.token})
        try:
            with urlopen(req,timeout=20) as r:return r.status,r.read().decode('utf-8')
        except HTTPError as e:return e.code,e.read().decode('utf-8')
    def prepare(self,k='N02_emi_impulses'):
        code,s=self.request('/api/demo',{'variant':k});self.assertEqual(code,200);c=json.loads(s)['config']
        code,s=self.request('/api/run',c);self.assertEqual(code,200);r=json.loads(s);return c,r
    def query(self,r,extra=''):
        i=r['results'][0]
        return '/api/trace?key='+i['evidence']['mapping']['key']+'&test_id='+i['id']+'&config_hash='+r['config_sha256']+'&record_hash='+r['record']['hashes']['cfg_sha256']+r['record']['hashes']['dat_sha256']+'&compare=1'+extra
    def test_analog_overlay_retains_original_peak(self):
        c,r=self.prepare();code,s=self.request(self.query(r,'&start=.825&end=.84'));self.assertEqual(code,200);d=json.loads(s)
        self.assertGreater(max(v for _,v in d['trace']),120);self.assertLess(max(v for _,v in d['processed_trace']),102)
        self.assertEqual(d['repair_marker_count'],2);self.assertLess(d['processed_source_samples'],20)
    def test_zoom_is_not_refiltered_locally(self):
        c,r=self.prepare();view,_,_=processed_record(self.state.record,c,r['results'][0]);expected=trace_window(view,'A1',.825,.84,bounds=[.25,2.])
        _,s=self.request(self.query(r,'&start=.825&end=.84'));self.assertEqual(json.loads(s)['processed_trace'],expected['trace'])
    def test_cache_identity_and_result_not_changed(self):
        c,r=self.prepare();before=json.dumps(self.state.result,sort_keys=True)
        self.request(self.query(r));cache_id=id(next(iter(self.state.processed_cache.values()))[0]);self.request(self.query(r,'&start=.7&end=.9'))
        self.assertEqual(cache_id,id(next(iter(self.state.processed_cache.values()))[0]));self.assertEqual(before,json.dumps(self.state.result,sort_keys=True))
    def test_new_record_clears_cache(self):
        c,r=self.prepare();self.request(self.query(r));self.assertTrue(self.state.processed_cache)
        self.request('/api/demo',{'variant':'N01_white_noise'});self.assertEqual(self.state.processed_cache,{})
    def test_new_config_clears_cache(self):
        c,r=self.prepare();self.request(self.query(r));c['tests'][0]['robust']['smooth_window_s']=0
        self.request('/api/run',c);self.assertEqual(self.state.processed_cache,{})
        self.assertEqual(self.request(self.query(r))[0],400)
    def test_digital_overlay_and_rejection_spans(self):
        c,r=self.prepare('N15_contact_bounce');code,s=self.request(self.query(r,'&start=.49&end=.53'));self.assertEqual(code,200)
        d=json.loads(s);self.assertTrue(d['rejected_spans']);self.assertTrue(d['processed_trace'])
    def test_unbound_compare_rejected(self):
        self.prepare();self.assertEqual(self.request('/api/trace?key=A1&compare=1')[0],400)
    def test_no_processing_for_legacy_assertion(self):
        c,r=self.prepare('pass');self.assertEqual(self.request(self.query(r))[0],400)
    def test_compare_parameter_validation(self):
        c,r=self.prepare();self.assertEqual(self.request(self.query(r).replace('compare=1','compare=maybe'))[0],400)
    def test_bad_run_invalidates_processed_cache(self):
        c,r=self.prepare();self.request(self.query(r));c['tests'][0]['robust']['unexpected']=1
        self.assertEqual(self.request('/api/run',c)[0],400);self.assertEqual(self.state.processed_cache,{})
    def test_report_exports_include_audit(self):
        c,r=self.prepare();code,s=self.request('/api/export/html');self.assertEqual(code,200)
        self.assertIn('原始与处理双分支',s);self.assertIn('class="processed"',s)
        code,s=self.request('/api/export/json');d=json.loads(s);self.assertIn('processing',d['results'][0]['evidence'])
        self.assertEqual(self.request('/api/export/csv')[0],200);self.assertEqual(self.request('/api/export/xml')[0],200)
    def test_invalid_protective_debounce_ui_returns_explicit_error(self):
        _,s=self.request('/api/demo',{'variant':'N18_invalid_debounce'});code,s=self.request('/api/run',json.loads(s)['config'])
        self.assertEqual(code,400);self.assertIn('合法保护脉冲',json.loads(s)['error'])
