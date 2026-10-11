"""Viewport correctness, bounds, decimation semantics and real HTTP transport."""
import copy,json,math,tempfile,threading,unittest
from array import array
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from esswave.generate import write_record,demo_suite,scenario
from esswave.comtrade import load
from esswave.engine import run
from esswave.trace import trace_window
from esswave.server import State,make_handler

class TraceViewportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();t=[i/10000 for i in range(20001)]
        cls.record=load(write_record(cls.temp.name,'trace',t,[{'name':'A','unit':'V','a':.01,'data':[125. if i==12345 else 100. for i in range(len(t))]},
            {'name':'skew','unit':'V','a':.01,'skew_us':750,'data':[2.]*len(t)}],
            [{'name':'dense','data':[i%2 for i in range(len(t))]}, {'name':'sparse','data':[int(1.2345<=x<1.2367) for x in t]}],fmt='FLOAT32'))
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def test_full_analog_keeps_single_sample_peak(self):
        d=trace_window(self.record,'A1');self.assertEqual(d['mode'],'minmax');self.assertEqual(max(p[1] for p in d['trace']),125)
        self.assertLessEqual(d['display_points'],1400)
    def test_zoom_rereads_original_samples(self):
        d=trace_window(self.record,'A1',1.233,1.236)
        self.assertEqual(d['mode'],'samples');self.assertLess(d['source_samples'],40)
        self.assertTrue(any(abs(p[0]-1.2345)<1e-9 and p[1]==125 for p in d['trace']))
    def test_dense_digital_is_not_faked_as_precise_edges(self):
        d=trace_window(self.record,'D1');self.assertEqual(d['mode'],'digital_envelope');self.assertEqual(d['trace'],[])
        self.assertTrue(d['digital_bins']);self.assertTrue(all(x[2:]==[0,1] for x in d['digital_bins']))
    def test_dense_digital_zoom_recovers_alternating_raw_samples(self):
        d=trace_window(self.record,'D1',1.234,1.235)
        self.assertEqual(d['mode'],'samples');v=[p[1] for p in d['trace']]
        self.assertTrue(all(a!=b for a,b in zip(v,v[1:])))
    def test_sparse_digital_preserves_exact_transition_neighbours(self):
        d=trace_window(self.record,'D2');self.assertEqual(d['mode'],'edges')
        edges=[d['trace'][i][0] for i in range(1,len(d['trace'])) if d['trace'][i][1]!=d['trace'][i-1][1]]
        self.assertAlmostEqual(edges[0],1.2345,8);self.assertAlmostEqual(edges[1],1.2367,8)
    def test_side_scaling_and_offset_in_result_view(self):
        d=trace_window(self.record,'A1',1.233,1.236,factor=.01,offset=-1,output_unit='kV')
        self.assertEqual(d['unit'],'kV');self.assertEqual(max(p[1] for p in d['trace']),.25)
    def test_channel_skew_affects_time_axis(self):
        d=trace_window(self.record,'A2');self.assertAlmostEqual(d['full_start_s'],.00075,10)
        self.assertAlmostEqual(d['full_end_s'],2.00075,10)
    def test_viewport_inside_one_sample_interval_has_brackets(self):
        d=trace_window(self.record,'A1',1.23451,1.23452)
        self.assertEqual(d['source_samples'],2);self.assertLessEqual(d['trace'][0][0],1.23451);self.assertGreaterEqual(d['trace'][-1][0],1.23452)
    def test_missing_sample_is_null_and_valid_json(self):
        r=copy.deepcopy(self.record);r.values['A1'][12345]=math.nan
        d=trace_window(r,'A1',1.233,1.236);self.assertTrue(any(p[1] is None for p in d['trace']))
        self.assertEqual(d['missing_samples'],1);json.dumps(d,allow_nan=False)
    def test_aggregated_missing_sample_remains_a_break(self):
        r=copy.deepcopy(self.record);r.values['A1'][12345]=math.nan
        self.assertTrue(any(p[1] is None for p in trace_window(r,'A1')['trace']))
    def test_data_gap_breaks_line_in_local_view(self):
        r=copy.deepcopy(self.record);r.gaps=[(1.2345,1.2346)]
        d=trace_window(r,'A1',1.23,1.24);self.assertEqual(d['gap_count'],1)
        self.assertTrue(any(p[1] is None for p in d['trace']))
    def test_many_gaps_respect_response_point_budget(self):
        r=copy.deepcopy(self.record);r.gaps=[(r.time[i-1],r.time[i]) for i in range(1,len(r.time),5)]
        d=trace_window(r,'A1',limit=64);self.assertLessEqual(d['display_points'],64);self.assertEqual(len(d['gaps']),256)
    def test_small_limit_never_breaks_bounds(self):
        for key in ('A1','D1','D2'):
            self.assertLessEqual(trace_window(self.record,key,limit=32)['display_points'],32)
    def test_timeline_queries_do_not_mutate_assertion_inputs(self):
        before=bytes(self.record.values['A1']);hashes=self.record.hashes.copy()
        for a,b in [(0,2),(1,1.1),(1.23,1.24),(0,.001)]:trace_window(self.record,'A1',a,b)
        self.assertEqual(bytes(self.record.values['A1']),before);self.assertEqual(hashes,self.record.hashes)
    def test_result_window_is_enforced(self):
        with self.assertRaises(ValueError):trace_window(self.record,'A1',0,2,bounds=[1,1.5])
    def test_whole_window_defaults_for_results(self):
        d=trace_window(self.record,'A1',bounds=[1,1.5]);self.assertEqual([d['start_s'],d['end_s']],[1,1.5])

for label,kwargs in {
 'nan_start':{'start':math.nan},'inf_end':{'end':math.inf},'negative_start':{'start':-.1},
 'too_late':{'end':2.1},'equal_times':{'start':1,'end':1},'reverse':{'start':1.1,'end':1},
 'boolean_time':{'start':True},'string_time':{'start':'0'},'limit_zero':{'limit':0},
 'limit_large':{'limit':8001},'limit_fraction':{'limit':100.1},'infinite_factor':{'factor':math.inf},
}.items():
    def test(self,kwargs=kwargs):
        with self.assertRaises(ValueError):trace_window(self.record,'A1',**kwargs)
    setattr(TraceViewportTests,'test_reject_'+label,test)

class TraceHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state=State();handler=make_handler(cls.state);handler.log_message=lambda *args:None
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.state.close()
    def setUp(self):
        cfg,cp=scenario(self.state.temp.name,'http','BINARY','pass');self.state.record=load(cfg)
        self.state.result=run(self.state.record,json.loads(cp.read_text()))
    def req(self,path):
        try:
            with urlopen(Request(self.url+path,headers={'X-ESS-Token':self.state.token}),timeout=10) as r:return r.status,r.read()
        except HTTPError as e:return e.code,e.read()
    def test_timeline_asset(self):self.assertEqual(self.req('/timeline.js')[0],200)
    def test_narrow_http_window_has_real_local_samples(self):
        status,body=self.req('/api/trace?key=A1&start=.3&end=.31');d=json.loads(body)
        self.assertEqual(status,200);self.assertEqual(d['mode'],'samples');self.assertLess(d['source_samples'],15)
    def test_stale_record_rejected(self):self.assertEqual(self.req('/api/trace?key=A1&record_hash=old')[0],400)
    def test_stale_result_rejected(self):self.assertEqual(self.req('/api/trace?test_id=A01&config_hash=old')[0],400)
    def test_result_trace_uses_evidence_domain(self):
        h=self.state.result['config_sha256'];status,body=self.req('/api/trace?test_id=A01&config_hash='+h)
        self.assertEqual(status,200);self.assertAlmostEqual(json.loads(body)['full_start_s'],.2,8)
    def test_unknown_result_test(self):self.assertEqual(self.req('/api/trace?test_id=no&config_hash='+self.state.result['config_sha256'])[0],400)
    def test_result_trace_requires_current_result(self):
        self.state.result=None;self.assertEqual(self.req('/api/trace?test_id=A01')[0],400)
    def test_missing_record(self):
        self.state.record=None;self.assertEqual(self.req('/api/trace?key=A1')[0],400)
    def test_unchanged_server_result_after_many_zooms(self):
        before=json.dumps(self.state.result,sort_keys=True)
        for a,b in [(0,2),(.1,.2),(.3,.31)]:self.assertEqual(self.req(f'/api/trace?key=A1&start={a}&end={b}')[0],200)
        self.assertEqual(json.dumps(self.state.result,sort_keys=True),before)

for label,query in {'duplicate':'key=A1&start=0&start=1','unknown':'key=A1&evil=1','nan':'key=A1&start=NaN',
    'infinite':'key=A1&end=Infinity','empty':'key=A1&start=','bad_limit':'key=A1&limit=100.5',
    'empty_key':'key=','reverse':'key=A1&start=1&end=.5','too_large':'key=A1&limit=9000'}.items():
    def test(self,query=query):self.assertEqual(self.req('/api/trace?'+query)[0],400)
    setattr(TraceHTTPTests,'test_http_reject_'+label,test)

if __name__=='__main__':unittest.main(verbosity=2)
