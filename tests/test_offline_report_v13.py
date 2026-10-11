"""Embedded raw data, resource budget and hostile metadata in offline reports."""
import copy,json,math,re,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from esswave.generate import scenario
from esswave.comtrade import load
from esswave.engine import run
from esswave.report import html_report
from esswave.interactive_report import full_trace,script_json
class OfflineReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();p,c=scenario(self.tmp.name,'one');self.record=load(p);self.config=json.loads(c.read_text());self.result=run(self.record,self.config)
    def tearDown(self):self.tmp.cleanup()
    def data(self,html):return json.loads(re.search(r'<script type="application/json" id="wave-report-data">(.*?)</script>',html,re.S).group(1))
    def test_original_window_samples_embedded(self):
        d=self.data(html_report(self.result,self.record));self.assertTrue(all(x['resolution']=='full' for x in d.values()));self.assertTrue(any(len(x['raw'])>1000 for x in d.values()))
    def test_summary_only_without_record_honest(self):self.assertTrue(all(x['resolution']=='summary' for x in self.data(html_report(self.result)).values()))
    def test_budget_zero_does_not_claim_raw_resolution(self):
        with patch('esswave.interactive_report.MAX_EMBED_POINTS',0):self.assertTrue(all(x['resolution']=='summary' for x in self.data(html_report(self.result,self.record)).values()))
    def test_js_injection_in_json_is_inert(self):
        text=script_json({'a':'</script><img onerror=1>&\u2028'});self.assertNotIn('</script>',text);self.assertEqual(json.loads(text)['a'],'</script><img onerror=1>&\u2028')
    def test_no_nan_literal_allowed(self):
        with self.assertRaises(ValueError):script_json({'a':math.nan})
    def test_missing_values_embedded_as_null(self):
        self.record.values['A1'][1000]=math.nan;t,_=full_trace(self.record,'A1',.99,1.01);self.assertTrue(any(p[1] is None for p in t))
    def test_gap_disconnected_in_full_trace(self):
        self.record.gaps=[(.999,1.003)];t,g=full_trace(self.record,'A1',.99,1.01);self.assertTrue(g);self.assertTrue(any(p[1] is None for p in t))
    def test_scale_applied_to_full_data(self):
        t,_=full_trace(self.record,'A1',.5,.51,2,3);ref,_=full_trace(self.record,'A1',.5,.51);self.assertEqual([x[1] for x in t],[2*x[1]+3 for x in ref])
    def test_hundreds_of_view_changes_cannot_mutate_results(self):
        snapshot=copy.deepcopy(self.result)
        for i in range(100):full_trace(self.record,'A1',.5+i*.001,.505+i*.001)
        self.assertEqual(self.result,snapshot)
    def test_self_contained_assets_and_explicit_zoom(self):
        h=html_report(self.result,self.record);self.assertIn('data-action="in"',h);self.assertIn('data-action="out"',h);self.assertIn('data-action="yin"',h);self.assertNotIn('<script src=',h);self.assertIn('class WaveTimeline',h)
