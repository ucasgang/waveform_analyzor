"""Real loopback HTTP tests, including origin/host/token gates and invalid uploads."""
import json
from pathlib import Path
import struct
import threading
from http.server import ThreadingHTTPServer
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from esswave.server import State,make_handler

ROOT=Path(__file__).resolve().parents[1]

class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state=State();handler=make_handler(cls.state);handler.log_message=lambda *args:None
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.state.close()
    def request(self,path,data=None,headers=None):
        h={'X-ESS-Token':self.state.token};h.update(headers or {})
        if data is not None and not isinstance(data,bytes):data=json.dumps(data).encode('utf-8')
        req=Request(self.url+path,data=data,headers=h)
        try:
            with urlopen(req,timeout=10) as r:return r.status,r.read(),dict(r.headers)
        except HTTPError as e:return e.code,e.read(),dict(e.headers)
    def test_index_contains_session_and_ui(self):
        s,b,h=self.request('/');self.assertEqual(s,200);self.assertIn(self.state.token.encode(),b);self.assertEqual(h['X-Frame-Options'],'DENY')
    def test_static_assets(self):
        for path in ('/app.js','/style.css'):
            self.assertEqual(self.request(path)[0],200)
    def test_unknown_path(self):self.assertEqual(self.request('/secret.txt')[0],404)
    def test_path_traversal_blocked(self):self.assertEqual(self.request('/../../etc/passwd')[0],404)
    def test_missing_token(self):self.assertEqual(self.request('/api/template',headers={'X-ESS-Token':''})[0],403)
    def test_wrong_token(self):self.assertEqual(self.request('/api/template',headers={'X-ESS-Token':'invalid'})[0],403)
    def test_cross_origin(self):self.assertEqual(self.request('/api/template',headers={'Origin':'https://evil.test'})[0],403)
    def test_dns_rebinding_host(self):self.assertEqual(self.request('/api/template',headers={'Host':'evil.test'})[0],403)
    def test_template(self):self.assertEqual(json.loads(self.request('/api/template')[1])['schema_version'],1)
    def test_demo_and_run(self):
        s,b,_=self.request('/api/demo',{'variant':'pass'});self.assertEqual(s,200);c=json.loads(b)['config']
        s,b,_=self.request('/api/run',c);self.assertEqual(s,200);self.assertEqual(json.loads(b)['status'],'PASS')
    def test_export_all_formats(self):
        _,b,_=self.request('/api/demo',{'variant':'pass'});self.request('/api/run',json.loads(b)['config'])
        for ext in ('html','json','csv','xml'):
            s,b,h=self.request('/api/export/'+ext);self.assertEqual(s,200);self.assertIn('attachment',h['Content-Disposition']);self.assertTrue(b)
    def test_inconclusive_through_api(self):
        _,b,_=self.request('/api/demo',{'variant':'gap'});c=json.loads(b)['config'];self.assertEqual(json.loads(self.request('/api/run',c)[1])['status'],'INCONCLUSIVE')
    def test_invalid_json(self):self.assertEqual(self.request('/api/run',b'{not json}')[0],400)
    def test_invalid_variant(self):self.assertEqual(self.request('/api/demo',{'variant':'../../bad'})[0],400)
    def test_upload_binary(self):
        cfg=(ROOT/'examples/01_pass_binary.cfg').read_bytes();dat=(ROOT/'examples/01_pass_binary.dat').read_bytes()
        s,b,_=self.request('/api/load',struct.pack('<I',len(cfg))+cfg+dat);self.assertEqual(s,200);self.assertEqual(json.loads(b)['record']['samples'],2001)
    def test_short_upload(self):self.assertEqual(self.request('/api/load',b'abc')[0],400)
    def test_invalid_cfg_length(self):self.assertEqual(self.request('/api/load',struct.pack('<I',99999999)+b'abc')[0],400)
    def test_bad_upload_clears_stale_record_and_results(self):
        _,b,_=self.request('/api/demo',{'variant':'pass'});self.request('/api/run',json.loads(b)['config'])
        self.request('/api/load',b'abc');self.assertIsNone(self.state.record);self.assertIsNone(self.state.result)
        self.assertEqual(self.request('/api/export/html')[0],400)
    def test_bad_run_clears_stale_result(self):
        _,b,_=self.request('/api/demo',{'variant':'pass'});c=json.loads(b)['config'];self.request('/api/run',c)
        c['tests'][0]['typo']=1;self.assertEqual(self.request('/api/run',c)[0],400);self.assertIsNone(self.state.result)
    def test_trace_endpoint(self):
        self.request('/api/demo',{'variant':'pass'});s,b,_=self.request('/api/trace?key=A1');self.assertEqual(s,200);self.assertLessEqual(len(json.loads(b)['trace']),1500)
    def test_bad_trace_key(self):
        self.request('/api/demo',{'variant':'pass'});self.assertEqual(self.request('/api/trace?key=missing')[0],400)
    def test_no_cors_header(self):self.assertNotIn('Access-Control-Allow-Origin',self.request('/')[2])
    def test_unknown_export(self):
        _,b,_=self.request('/api/demo',{'variant':'pass'});self.request('/api/run',json.loads(b)['config']);self.assertEqual(self.request('/api/export/exe')[0],404)

if __name__=='__main__':unittest.main(verbosity=2)
