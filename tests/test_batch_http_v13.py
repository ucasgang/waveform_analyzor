"""Real loopback endpoints, malformed request isolation and recursive CLI checks."""
import io,json,struct,tempfile,threading,time,unittest,zipfile
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from esswave.server import State,make_handler
from esswave.generate import scenario
from esswave.cli import batch

class BatchHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.cfg,cls.case=scenario(cls.tmp.name,'source');cls.dat=cls.cfg.with_suffix('.dat')
        cls.state=State();h=make_handler(cls.state);h.log_message=lambda *a:None
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),h);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start();cls.url=f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.state.close();cls.tmp.cleanup()
    def setUp(self):
        s=self.state.batch.snapshot()
        if s.get('id'):
            self.state.batch.cancel(s['id'])
            if self.state.batch.worker:self.state.batch.worker.join()
    def req(self,path,data=None,headers=None):
        h={'X-ESS-Token':self.state.token};h.update(headers or {})
        raw=data if isinstance(data,bytes) else (json.dumps(data).encode() if data is not None else None)
        try:
            with urlopen(Request(self.url+path,data=raw,headers=h),timeout=20) as r:return r.status,r.read(),dict(r.headers)
        except HTTPError as e:return e.code,e.read(),dict(e.headers)
    def create(self):
        files=[{'path':'root/a/x'+p.suffix,'size':p.stat().st_size} for p in (self.cfg,self.dat,self.case)]
        status,b,_=self.req('/api/batch/create',{'files':files,'mode':'sibling'});self.assertEqual(status,200);return json.loads(b)
    def finish(self):
        s=self.create();a,b,c=self.cfg.read_bytes(),self.dat.read_bytes(),self.case.read_bytes()
        self.assertEqual(self.req('/api/batch/upload/'+s['id']+'/r00001',struct.pack('<II',len(a),len(c))+a+b+c)[0],200)
        self.assertEqual(self.req('/api/batch/start/'+s['id'],{})[0],200)
        for _ in range(200):
            done=json.loads(self.req('/api/batch/status/'+s['id'])[1])
            if done['state']=='COMPLETE':return done
            time.sleep(.01)
        self.fail('timeout')
    def test_token_gate_applies_to_all_batch_reads(self):
        for path in ('current','status/missing','export/missing'):
            self.assertEqual(self.req('/api/batch/'+path,headers={'X-ESS-Token':''})[0],403)
    def test_cross_origin_cannot_create(self):self.assertEqual(self.req('/api/batch/create',{}, {'Origin':'https://outside.invalid'})[0],403)
    def test_missing_action_404_not_server_crash(self):self.assertEqual(self.req('/api/batch/',{})[0],404)
    def test_unknown_action_404(self):self.assertEqual(self.req('/api/batch/erase',{})[0],404)
    def test_array_payload_400(self):self.assertEqual(self.req('/api/batch/create',[])[0],400)
    def test_null_payload_400(self):self.assertEqual(self.req('/api/batch/create',b'null')[0],400)
    def test_duplicate_payload_keys_400(self):self.assertEqual(self.req('/api/batch/create',b'{"files":[],"files":[]}')[0],400)
    def test_start_nonempty_body_400(self):s=self.create();self.assertEqual(self.req('/api/batch/start/'+s['id'],{'silently':'ignore'})[0],400)
    def test_cancel_array_body_400(self):s=self.create();self.assertEqual(self.req('/api/batch/cancel/'+s['id'],[])[0],400)
    def test_unknown_id_400(self):self.assertEqual(self.req('/api/batch/status/not-present')[0],400)
    def test_batch_error_does_not_clear_single_record(self):
        _,b,_=self.req('/api/demo',{'variant':'pass'});self.req('/api/run',json.loads(b)['config']);old=self.state.result
        self.req('/api/batch/create',[]);self.assertIs(self.state.result,old);self.assertIsNotNone(self.state.record)
    def test_zip_repeated_download_stable_and_open_bound(self):
        s=self.finish();url='/api/batch/export/'+s['id'];one=self.req(url);two=self.req(url)
        self.assertEqual(one[0],200);self.assertEqual(one[1],two[1]);self.assertEqual(one[2]['Content-Type'],'application/zip')
        with zipfile.ZipFile(io.BytesIO(one[1])) as z:self.assertIsNone(z.testzip());self.assertIn('summary.csv',z.namelist())
        status,b,_=self.req('/api/batch/open/'+s['id']+'/r00001',{});self.assertEqual(status,200);d=json.loads(b);self.assertEqual(d['result']['status'],'PASS')
        self.assertEqual(self.req('/api/trace?key=A1&record_hash=old')[0],400)
        self.assertEqual(self.req('/api/trace?key=A1&start=.51&end=.515')[0],200)
    def test_pending_open_rejected(self):s=self.create();self.assertEqual(self.req('/api/batch/open/'+s['id']+'/r00001',{})[0],400)
    def test_new_static_assets_present(self):
        for path in ('/plot.js','/batch.js','/timeline.js'):self.assertEqual(self.req(path)[0],200)

class DirectoryCLITests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.source=self.root/'source'
    def tearDown(self):self.tmp.cleanup()
    def test_recursive_siblings_duplicate_stems_independent(self):
        scenario(self.source/'A','same');scenario(self.source/'B','same',variant='overshoot')
        r=batch(directory=self.source,sibling_cases=True,recursive=True,out=self.root/'out');self.assertEqual(r['records'],2);self.assertEqual(r['status'],'FAIL');self.assertEqual({x['status'] for x in r['cases']},{'PASS','FAIL'})
    def test_orphan_dat_included_error(self):
        scenario(self.source,'good');(self.source/'orphan.dat').write_bytes(b'x')
        r=batch(directory=self.source,sibling_cases=True,out=self.root/'out');self.assertEqual(r['records'],2);self.assertEqual(r['status'],'ERROR')
    def test_no_recursive_does_not_execute_nested(self):
        scenario(self.source,'root');scenario(self.source/'deep','child',variant='overshoot')
        r=batch(directory=self.source,sibling_cases=True,out=self.root/'out');self.assertEqual(r['records'],1);self.assertEqual(r['status'],'PASS')
    def test_common_config_uppercase_dat(self):
        cfg,case=scenario(self.source,'same');cfg.with_suffix('.dat').rename(cfg.with_suffix('.DAT'))
        r=batch(directory=self.source,case=case,out=self.root/'out');self.assertEqual(r['status'],'PASS')
