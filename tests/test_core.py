"""Regression, golden vectors, metamorphic checks and malformed-input tests.
No test dependency beyond the Python standard library.
"""
from __future__ import annotations
from array import array
import copy
import io
import json
import math
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from esswave.comtrade import load,parse_cfg,ComtradeError
from esswave.engine import run,validate,resolve,ConfigError,debounced,criterion,overall,compact_trace
from esswave.generate import write_record,scenario,demo_suite,FORMATS
from esswave.report import html_report,csv_text,junit_text,export

ROOT=Path(__file__).resolve().parents[1]


def cfg_for(test,channels=None):
    return {"schema_version":1,"name":"unit","channels":channels or {"a":{"kind":"analog","name":"A","unit":"V"},"d":{"kind":"digital","name":"D"}},"tests":[test]}


def range_test():return {"id":"a","type":"analog_range","channel":"a","window":[0,.1],"min":0,"max":100}


def step_test(target=100):
    return {"id":"s","type":"analog_step","channel":"a","trigger":{"type":"time","at":.2},"window":[0,1.5],"baseline_window":[-.1,-.01],"target":target,"normalization":"rated","rated":100,"steady_last_s":.25,"settle_band_pct":2,"settle_hold_s":.1,"limits":{"overshoot_pct_max":5,"response_s_max":.4,"settling_s_max":.7,"steady_error_pct_max":1}}

class TempCase(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def record(self,fmt="FLOAT32",revision=2013,times=None,ys=None,ds=None,ach=None,dch=None,**kw):
        t=times or [i*.001 for i in range(101)]
        aa=ach if ach is not None else [{"name":"A","unit":"V","a":.01,"data":ys or [i*.1 for i in range(len(t))]}]
        dd=dch if dch is not None else [{"name":"D","data":ds or [0 if x<.03 or x>=.05 else 1 for x in t]}]
        return load(write_record(self.folder,"test",t,aa,dd,fmt=fmt,revision=revision,**kw))
    def demo(self,variant="pass",fmt="BINARY"):
        cfg,case=scenario(self.folder,variant,fmt,variant)
        return load(cfg),json.loads(case.read_text(encoding="utf-8"))

class ParserTests(TempCase):
    def test_handwritten_binary_golden_vector(self):
        # This vector is literal, not produced by the fixture writer.
        # Two rows: sample 1/time0/rawA100/status bit0=1; sample2/time1000/rawA-20/bit0=0.
        cfg='GOLD,UNIT,1999\n2,1A,1D\n1,A,,,V,0.5,-2,0,-32767,32767,1,1,P\n1,D,,,0\n50\n1\n1000,2\n01/01/2026,00:00:00.000000\n01/01/2026,00:00:00.000000\nBINARY\n1\n'
        (self.folder/'gold.cfg').write_text(cfg)
        (self.folder/'gold.dat').write_bytes(bytes.fromhex('01000000000000006400010002000000e8030000ecff0000'))
        r=load(self.folder/'gold.cfg')
        self.assertEqual(list(r.values['A1']),[48.,-12.]);self.assertEqual(list(r.values['D1']),[1,0]);self.assertEqual(list(r.time),[0,.001])
    def test_lsb_packing_across_word_boundaries(self):
        t=[i*.001 for i in range(10)]
        ds=[{'name':f'D{i}','data':[int((i+j)%3==0) for j in range(10)]} for i in range(37)]
        r=self.record(fmt='BINARY',times=t,ach=[],dch=ds)
        for i in range(37):self.assertEqual(list(r.values[f'D{i+1}']),ds[i]['data'])
    def test_analog_only(self):self.assertEqual(self.record(dch=[]).meta['digital_count'],0)
    def test_digital_only(self):self.assertEqual(self.record(ach=[]).meta['analog_count'],0)
    def test_gain_and_offset(self):
        t=[i*.001 for i in range(101)]; y=[1+i*.02 for i in range(101)]
        r=self.record(ach=[{'name':'A','unit':'V','a':-.02,'b':3,'data':y}],times=t)
        self.assertAlmostEqual(r.values['A1'][90],y[90],6)
    def test_gbk(self):
        r,c=self.demo('gbk');self.assertEqual(r.channels[0].name,'有功功率');self.assertEqual(r.meta['encoding'],'gb18030')
    def test_utf8_bom(self):
        self.record();p=self.folder/'test.cfg';p.write_bytes(b'\xef\xbb\xbf'+p.read_bytes());self.assertEqual(load(p).meta['analog_count'],1)
    def test_case_insensitive_dat_extension(self):
        self.record();p=self.folder/'test.dat';p.rename(self.folder/'TEST.DAT');self.assertEqual(len(load(self.folder/'test.cfg').time),101)
    def test_duplicate_dat_match(self):
        self.record();(self.folder/'TEST.DAT').write_bytes((self.folder/'test.dat').read_bytes())
        with self.assertRaises(ComtradeError):load(self.folder/'test.cfg')
    def test_rebuild_multi_rate(self):
        t=[0,.001,.002,.004,.006,.008]
        r=self.record(times=t,zero_timestamps=True,rates=[(1000,3),(500,6)])
        for a,b in zip(r.time,t):self.assertAlmostEqual(a,b,10)
        self.assertEqual(r.meta['time_source'],'sample_rates')
    def test_valid_multi_rate_timestamps(self):
        r=self.record(times=[0,.001,.002,.004,.006,.008],rates=[(1000,3),(500,6)])
        self.assertEqual(r.gaps,[])
    def test_zero_timestamps_no_rates(self):
        with self.assertRaises(ComtradeError):self.record(zero_timestamps=True,omit_rates=True)
    def test_all_missing_timestamps_reconstructed(self):
        r=self.record(raw_override=lambda j,r:[r[0],4294967295]+r[2:]);self.assertEqual(r.meta['time_source'],'sample_rates')
    def test_partially_missing_timestamps_rejected(self):
        with self.assertRaises(ComtradeError):self.record(raw_override=lambda j,r:[r[0],4294967295 if j==2 else r[1]]+r[2:])
    def test_duplicate_timestamps(self):
        with self.assertRaises(ComtradeError):self.record(raw_override=lambda j,r:[r[0],1000 if j==2 else r[1]]+r[2:])
    def test_reverse_timestamps(self):
        with self.assertRaises(ComtradeError):self.record(raw_override=lambda j,r:[r[0],0 if j==5 else r[1]]+r[2:])
    def test_sample_number_gap(self):
        with self.assertRaises(ComtradeError):self.record(raw_override=lambda j,r:[r[0]+1 if j==5 else r[0]]+r[1:])
    def test_fractional_sample_number(self):
        with self.assertRaises(ComtradeError):self.record(fmt='ASCII',raw_override=lambda j,r:[1.5 if j==0 else r[0]]+r[1:])
    def test_time_multiplier(self):
        r=self.record(timemult=10);self.assertAlmostEqual(r.time[50],.05,10)
    def test_truncated_binary_record(self):
        self.record(fmt='BINARY');p=self.folder/'test.dat';p.write_bytes(p.read_bytes()[:-1])
        with self.assertRaises(ComtradeError):load(self.folder/'test.cfg')
    def test_truncated_complete_binary_row(self):
        self.record(fmt='BINARY');p=self.folder/'test.dat';p.write_bytes(p.read_bytes()[:-12])
        with self.assertRaises(ComtradeError):load(self.folder/'test.cfg')
    def test_extra_sample_rows(self):
        self.record(fmt='ASCII');p=self.folder/'test.dat';p.write_bytes(p.read_bytes()+b'102,101000,0,0\r\n')
        with self.assertRaises(ComtradeError):load(self.folder/'test.cfg')
    def test_empty_ascii_row(self):
        self.record(fmt='ASCII');p=self.folder/'test.dat';p.write_bytes(p.read_bytes()+b'\n')
        with self.assertRaises(ComtradeError):load(self.folder/'test.cfg')
    def test_invalid_digital_value(self):
        with self.assertRaises(ComtradeError):self.record(fmt='ASCII',raw_override=lambda j,r:r[:-1]+[2] if j==3 else r)
    def test_ascii_missing_column(self):
        with self.assertRaises(ComtradeError):self.record(fmt='ASCII',raw_override=lambda j,r:r[:-1] if j==3 else r)
    def test_bad_ascii_analog_value(self):
        with self.assertRaises(ComtradeError):self.record(fmt='ASCII',raw_override=lambda j,r:r[:2]+['hello']+r[3:] if j==3 else r)
    def test_ascii_nan_not_inferred_as_valid(self):
        with self.assertRaises(ComtradeError):self.record(fmt='ASCII',raw_override=lambda j,r:r[:2]+['NaN']+r[3:] if j==3 else r)
    def test_cfg_bad_count(self):
        self.record();p=self.folder/'test.cfg';p.write_text(p.read_text().replace('2,1A,1D','3,1A,1D'))
        with self.assertRaises(ComtradeError):load(p)
    def test_cfg_bad_revision(self):
        self.record();p=self.folder/'test.cfg';p.write_text(p.read_text().replace('2013','2020',1))
        with self.assertRaises(ComtradeError):load(p)
    def test_cfg_zero_gain(self):
        with self.assertRaises(ComtradeError):parse_cfg(b'S,D,2013\n1,1A,0D\n1,A,,,V,0,0,0,-1,1,1,1,P\n')
    def test_cfg_nan_gain(self):
        with self.assertRaises(ComtradeError):parse_cfg(b'S,D,2013\n1,1A,0D\n1,A,,,V,NaN,0,0,-1,1,1,1,P\n')
    def test_cfg_unknown_extension(self):
        self.record(revision=1999,fmt='BINARY');p=self.folder/'test.cfg';p.write_text(p.read_text()+'vendor extension\n')
        with self.assertRaises(ComtradeError):load(p)
    def test_memory_guard(self):
        self.record()
        with self.assertRaises(ComtradeError):load(self.folder/'test.cfg',max_memory_mb=0)
    def test_cff_explicit_rejection(self):
        with self.assertRaises(ComtradeError):load(self.folder/'test.cff')
    def test_cfg_truncation(self):
        with self.assertRaises(ComtradeError):parse_cfg(b'S,D,2013\n1,1A,0D\n')
    def test_no_rates_valid_dat(self):
        r=self.record(omit_rates=True);self.assertAlmostEqual(r.time[-1],.1)
    def test_gap_detected(self):
        r=self.record(times=[i*.001+(0 if i<40 else .01) for i in range(101)]);self.assertEqual(len(r.gaps),1)
    def test_invalid_date(self):
        self.record();p=self.folder/'test.cfg';p.write_text(p.read_text().replace('01/01/2026','32/01/2026'))
        with self.assertRaises(ComtradeError):load(p)
    def test_metadata_nano_fraction(self):
        self.record();p=self.folder/'test.cfg';s=p.read_text().replace('00:00:00.000000','00:00:00.000000001').replace('00:00:00.200000','00:00:00.200000123');p.write_text(s)
        self.assertAlmostEqual(load(p).meta['trigger_s'],.200000122,12)

class MappingTests(TempCase):
    def test_exact_name(self):
        r=self.record();self.assertEqual(resolve(r,'a',cfg_for(range_test())).channel.key,'A1')
    def test_index(self):
        r=self.record();c=cfg_for(range_test(),{'a':{'kind':'analog','index':1,'unit':'V'}});self.assertEqual(run(r,c)['status'],'PASS')
    def test_alternative_names(self):
        r=self.record();c=cfg_for(range_test());c['channels']['a'].pop('name');c['channels']['a']['names']=['other','A'];self.assertEqual(run(r,c)['status'],'PASS')
    def test_glob_unique(self):
        r=self.record();c=cfg_for(range_test());c['channels']['a'].pop('name');c['channels']['a']['pattern']='A*';self.assertEqual(run(r,c)['status'],'PASS')
    def test_duplicate_name_rejected(self):
        t=[i*.001 for i in range(101)];a=[{'name':'A','unit':'V','data':[1]*101}]*2;r=self.record(times=t,ach=a)
        self.assertEqual(run(r,cfg_for(range_test()))['status'],'ERROR')
    def test_missing_channel_is_error(self):
        r=self.record();c=cfg_for(range_test());c['channels']['a']['name']='missing';self.assertEqual(run(r,c)['status'],'ERROR')
    def test_unit_mismatch(self):
        r=self.record();c=cfg_for(range_test());c['channels']['a']['unit']='kV';self.assertEqual(run(r,c)['status'],'ERROR')
    def test_empty_unit(self):
        r=self.record(ach=[{'name':'A','unit':'','data':[1]*101}]);c=cfg_for(range_test());del c['channels']['a']['unit'];self.assertEqual(run(r,c)['status'],'ERROR')
    def test_primary_conversion(self):
        r=self.record(ach=[{'name':'A','unit':'V','data':[1]*101,'primary':1000,'secondary':100,'side':'S'}]);c=cfg_for(range_test());c['channels']['a']['side']='primary';self.assertEqual(resolve(r,'a',c).values[0],10)
    def test_secondary_conversion(self):
        r=self.record(ach=[{'name':'A','unit':'V','data':[100]*101,'primary':1000,'secondary':100,'side':'P'}]);c=cfg_for(range_test());c['channels']['a']['side']='secondary';self.assertEqual(resolve(r,'a',c).values[0],10)
    def test_scale_offset_explicit(self):
        r=self.record(ys=[1]*101);c=cfg_for(range_test());c['channels']['a'].update(scale=2,offset=3);self.assertEqual(resolve(r,'a',c).values[0],5)
    def test_explicit_output_unit_conversion(self):
        r=self.record(ys=[1000]*101);c=cfg_for(range_test());c['channels']['a'].update(scale=.001,output_unit='kV')
        sig=resolve(r,'a',c);self.assertEqual(sig.unit,'kV');self.assertEqual(sig.values[0],1.)
    def test_changed_unit_without_scale_rejected(self):
        r=self.record();c=cfg_for(range_test());c['channels']['a']['output_unit']='kV'
        self.assertEqual(run(r,c)['status'],'ERROR')
    def test_1991_ratio_conversion_not_inferred(self):
        r=self.record(fmt='BINARY',revision=1991);c=cfg_for(range_test());c['channels']['a']['side']='primary'
        self.assertEqual(run(r,c)['status'],'ERROR')
    def test_skew_applied(self):
        r=self.record(ach=[{'name':'A','unit':'V','data':[1]*101,'skew_us':500}]);s=resolve(r,'a',cfg_for(range_test()));self.assertAlmostEqual(s.time[0],.0005)

class DigitalTests(TempCase):
    def dconfig(self,typ='digital_edge',**kw):
        a={'id':'d','type':typ,'channel':'d','window':[0,.1],**kw};return cfg_for(a)
    def test_rising(self):self.assertEqual(run(self.record(),self.dconfig(edge='rising',count=1))['status'],'PASS')
    def test_falling(self):self.assertEqual(run(self.record(),self.dconfig(edge='falling',count=1))['status'],'PASS')
    def test_both_edges(self):self.assertEqual(run(self.record(),self.dconfig(edge='any',count=2))['status'],'PASS')
    def test_missing_edge_fails(self):self.assertEqual(run(self.record(ds=[0]*101),self.dconfig(count=1))['status'],'FAIL')
    def test_absence(self):self.assertEqual(run(self.record(ds=[0]*101),self.dconfig(count=0))['status'],'PASS')
    def test_latency_exact_boundary(self):
        r=run(self.record(),self.dconfig(latency_min_s=.03,latency_max_s=.03));self.assertEqual(r['status'],'PASS')
    def test_latency_guard_band(self):
        self.assertEqual(run(self.record(),self.dconfig(latency_max_s=.03,guard_band_s=.001))['status'],'INCONCLUSIVE')
    def test_pulse_width(self):
        r=run(self.record(),self.dconfig('digital_pulse',min_width_s=.02,max_width_s=.02));self.assertEqual(r['status'],'PASS')
    def test_low_active_pulse(self):
        ds=[1 if i<30 or i>=50 else 0 for i in range(101)]
        self.assertEqual(run(self.record(ds=ds),self.dconfig('digital_pulse',active=0,min_width_s=.019,max_width_s=.021))['status'],'PASS')
    def test_narrow_pulse_fail(self):self.assertEqual(run(self.record(),self.dconfig('digital_pulse',min_width_s=.021))['status'],'FAIL')
    def test_open_pulse_inconclusive(self):self.assertEqual(run(self.record(ds=[int(i>=30) for i in range(101)]),self.dconfig('digital_pulse',min_width_s=.02))['status'],'INCONCLUSIVE')
    def test_left_truncated_pulse(self):self.assertEqual(run(self.record(ds=[int(i<30) for i in range(101)]),self.dconfig('digital_pulse',min_width_s=.02))['status'],'INCONCLUSIVE')
    def test_pulse_at_exact_window_start(self):
        c=self.dconfig('digital_pulse',min_width_s=.02,max_width_s=.02);c['tests'][0]['window']=[.03,.1];self.assertEqual(run(self.record(),c)['status'],'PASS')
    def test_hold_high(self):self.assertEqual(run(self.record(ds=[1]*101),self.dconfig('digital_hold',value=1))['status'],'PASS')
    def test_hold_low(self):self.assertEqual(run(self.record(ds=[0]*101),self.dconfig('digital_hold',value=0))['status'],'PASS')
    def test_hold_detects_single_sample_glitch(self):
        ds=[0]*101;ds[50]=1;self.assertEqual(run(self.record(ds=ds),self.dconfig('digital_hold',value=0))['status'],'FAIL')
    def test_explicit_debounce_ignores_glitch(self):
        ds=[0]*101;ds[50]=1;self.assertEqual(run(self.record(ds=ds),self.dconfig('digital_hold',value=0,debounce_s=.002))['status'],'PASS')
    def test_debounce_retains_real_event_onset(self):
        r=run(self.record(),self.dconfig(debounce_s=.003));self.assertEqual(r['status'],'PASS');self.assertAlmostEqual(r['results'][0]['metrics']['latencies_s'][0],.03)
    def test_pending_end_not_silent_pass(self):
        ds=[0]*101;ds[-1]=1;self.assertEqual(run(self.record(ds=ds),self.dconfig('digital_hold',value=0,debounce_s=.003))['status'],'INCONCLUSIVE')
    def test_pending_transition_before_fractional_window(self):
        ds=[0]*101;ds[-2:]=[1,1];c=self.dconfig('digital_hold',value=0,debounce_s=.003)
        c['tests'][0]['window']=[.0995,.1]
        self.assertEqual(run(self.record(ds=ds),c)['status'],'INCONCLUSIVE')
    def test_trigger_occurrence(self):
        ds=[int(20<=i<30 or i>=60) for i in range(101)];c=self.dconfig('digital_hold',value=1)
        c['tests'][0].update(trigger={'type':'digital_edge','channel':'d','occurrence':2},window=[0,.03]);self.assertEqual(run(self.record(ds=ds),c)['status'],'PASS')
    def test_trigger_not_found(self):
        c=self.dconfig();c['tests'][0]['trigger']={'type':'digital_edge','channel':'d','occurrence':3};self.assertEqual(run(self.record(),c)['status'],'INCONCLUSIVE')
    def test_no_preceding_sample_no_invented_edge(self):
        ds=[1]*101;self.assertEqual(run(self.record(ds=ds),self.dconfig())['status'],'FAIL')

class AnalogTests(TempCase):
    def test_range_fail(self):
        c=cfg_for(range_test());c['tests'][0]['max']=5;self.assertEqual(run(self.record(),c)['status'],'FAIL')
    def test_out_of_record_window(self):
        c=cfg_for(range_test());c['tests'][0]['window']=[-.1,.1];self.assertEqual(run(self.record(),c)['status'],'INCONCLUSIVE')
    def test_missing_selected_channel(self):
        y=[1]*101;y[20]=None;self.assertEqual(run(self.record(ys=y),cfg_for(range_test()))['status'],'INCONCLUSIVE')
    def test_missing_unused_channel_does_not_poison(self):
        t=[i*.001 for i in range(101)];a=[{'name':'A','unit':'V','data':[1]*101},{'name':'unused','unit':'V','data':[None]*101}]
        self.assertEqual(run(self.record(times=t,ach=a),cfg_for(range_test()))['status'],'PASS')
    def test_mean_time_weighted_irregular(self):
        r=self.record(times=[0,.01,.1],ys=[0,1,10],omit_rates=True)
        # Explicit test of time integration without inferred-gap ambiguity.
        r.gaps=[];res=run(r,cfg_for(range_test()));self.assertAlmostEqual(res['results'][0]['metrics']['mean'],5.,8)
    def test_linear_rms_exact(self):
        r=self.record(times=[0,.05,.1],ys=[0,5,10]);c=cfg_for({'id':'r','type':'analog_rms','channel':'a','window':[0,.1],'min':5.77,'max':5.78})
        res=run(r,c);self.assertEqual(res['status'],'PASS');self.assertAlmostEqual(res['results'][0]['metrics']['rms'],10/math.sqrt(3),8)
    def test_rms_constant(self):
        r=self.record(ys=[-3]*101);c=cfg_for({'id':'r','type':'analog_rms','channel':'a','window':[0,.1],'min':3,'max':3});self.assertEqual(run(r,c)['status'],'PASS')
    def test_max_gap_policy(self):
        c=cfg_for(range_test());c['tests'][0]['max_gap_s']=.0005;self.assertEqual(run(self.record(),c)['status'],'INCONCLUSIVE')
    def test_analog_crossing_trigger(self):
        c=cfg_for(range_test());c['tests'][0].update(trigger={'type':'analog_crossing','channel':'a','threshold':3,'edge':'rising'},window=[0,.03])
        res=run(self.record(),c);self.assertEqual(res['status'],'PASS');self.assertAlmostEqual(res['results'][0]['evidence']['trigger']['at'],.03,6)
    def test_analog_falling_trigger(self):
        c=cfg_for(range_test());c['tests'][0].update(trigger={'type':'analog_crossing','channel':'a','threshold':7,'edge':'falling'},window=[0,.03]);res=run(self.record(ys=[10-i*.1 for i in range(101)]),c);self.assertEqual(res['status'],'PASS')
    def test_zero_step_is_inconclusive(self):
        t=[i*.001 for i in range(2001)];r=self.record(times=t,ys=[100]*len(t));self.assertEqual(run(r,cfg_for(step_test()))['status'],'INCONCLUSIVE')
    def test_target_zero_denominator_error(self):
        r,c=self.demo('zero_target');c['tests']=[c['tests'][3]];c['tests'][0]['normalization']='target';self.assertEqual(run(r,c)['status'],'ERROR')
    def test_target_from_channel(self):
        r,c=self.demo();r.channels.append(type(r.channels[0])('A4',4,'analog','setpoint','kW'));r.values['A4']=array('d',[100]*len(r.time))
        c['channels']['setpoint']={'kind':'analog','name':'setpoint','unit':'kW'};a=c['tests'][3];a.pop('target');a.update(target_channel='setpoint',target_window=[.1,.2]);c['tests']=[a];self.assertEqual(run(r,c)['status'],'PASS')
    def test_varying_target_inconclusive(self):
        r,c=self.demo();a=c['tests'][3];a.pop('target');a.update(target_channel='power',target_window=[0,.5]);c['tests']=[a];self.assertEqual(run(r,c)['status'],'INCONCLUSIVE')
    def test_short_observation_never_response(self):
        r,c=self.demo('never_response');a=c['tests'][3];a.update(window=[0,.2],steady_last_s=.05);a['limits']={'response_s_max':.4};c['tests']=[a];self.assertEqual(run(r,c)['status'],'INCONCLUSIVE')
    def test_pretrigger_response_inconclusive(self):
        r,c=self.demo();r.values['A1']=array('d',[0 if t<.19 else 100 for t in r.time]);a=c['tests'][3];a['limits']={'response_s_max':.4};c['tests']=[a];self.assertEqual(run(r,c)['status'],'INCONCLUSIVE')
    def test_oscillation_not_first_band_entry(self):
        r,c=self.demo('oscillation');res=run(r,c);a=res['results'][3];settle=next(x for x in a['checks'] if x['metric']=='settling_s');self.assertEqual(settle['status'],'FAIL')
    def test_sampling_offset_window_interpolation(self):
        r=self.record();a=range_test();a['window']=[.0005,.0995];res=run(r,cfg_for(a));self.assertAlmostEqual(res['results'][0]['metrics']['mean'],5.,5)

class ValidationTests(unittest.TestCase):
    def invalid(self,fn):
        c=demo_suite();fn(c)
        with self.assertRaises(ConfigError):validate(c)
    def test_unknown_root(self):self.invalid(lambda c:c.update(typo=True))
    def test_bad_version(self):self.invalid(lambda c:c.update(schema_version=True))
    def test_empty_tests(self):self.invalid(lambda c:c.update(tests=[]))
    def test_duplicate_id(self):self.invalid(lambda c:c['tests'].append(copy.deepcopy(c['tests'][0])))
    def test_unknown_assertion(self):self.invalid(lambda c:c['tests'][0].update(type='python_eval'))
    def test_unknown_test_field(self):self.invalid(lambda c:c['tests'][0].update(latency_ms=100))
    def test_negative_window(self):self.invalid(lambda c:c['tests'][0].update(window=[1,0]))
    def test_missing_channel_alias(self):self.invalid(lambda c:c['tests'][0].update(channel='x'))
    def test_mismatched_kind(self):self.invalid(lambda c:c['tests'][0].update(channel='power'))
    def test_boolean_count(self):self.invalid(lambda c:c['tests'][0].update(count=True))
    def test_noninteger_count(self):self.invalid(lambda c:c['tests'][0].update(count=1.2))
    def test_negative_debounce(self):self.invalid(lambda c:c['tests'][0].update(debounce_s=-1))
    def test_string_limit(self):self.invalid(lambda c:c['tests'][0].update(latency_max_s='0.1'))
    def test_nan_limit(self):self.invalid(lambda c:c['tests'][0].update(latency_max_s=float('nan')))
    def test_infinite_limit(self):self.invalid(lambda c:c['tests'][3]['limits'].update(response_s_max=float('inf')))
    def test_unknown_limit(self):self.invalid(lambda c:c['tests'][3]['limits'].update(response_ms_max=100))
    def test_empty_limit(self):self.invalid(lambda c:c['tests'][3].update(limits={}))
    def test_digital_scale(self):self.invalid(lambda c:c['channels']['command'].update(scale=2))
    def test_zero_scale(self):self.invalid(lambda c:c['channels']['power'].update(scale=0))
    def test_multiple_selectors(self):self.invalid(lambda c:c['channels']['power'].update(index=1))
    def test_empty_alternative_names(self):self.invalid(lambda c:c['channels'].update(power={'kind':'analog','names':[]}))
    def test_bad_trigger(self):self.invalid(lambda c:c['tests'][0]['trigger'].update(type='python'))
    def test_trigger_wrong_kind(self):self.invalid(lambda c:c['tests'][0]['trigger'].update(channel='power'))
    def test_missing_analog_threshold(self):self.invalid(lambda c:c['tests'][0].update(trigger={'type':'analog_crossing','channel':'power'}))
    def test_reversed_latency_limits(self):self.invalid(lambda c:c['tests'][0].update(latency_min_s=1,latency_max_s=.1))
    def test_pulse_requires_limits(self):self.invalid(lambda c:(c['tests'][1].pop('min_width_s'),c['tests'][1].pop('max_width_s')))
    def test_bad_hold_value(self):self.invalid(lambda c:c['tests'][2].update(value=2))
    def test_two_targets(self):self.invalid(lambda c:c['tests'][3].update(target_channel='power',target_window=[0,.1]))
    def test_step_window_must_start_at_trigger(self):self.invalid(lambda c:c['tests'][3].update(window=[.1,1.5]))
    def test_baseline_after_trigger(self):self.invalid(lambda c:c['tests'][3].update(baseline_window=[0,.1]))
    def test_zero_rated(self):self.invalid(lambda c:c['tests'][3].update(rated=0))
    def test_invalid_fraction_order(self):self.invalid(lambda c:c['tests'][3].update(low_fraction=.9,high_fraction=.1))
    def test_steady_window_too_long(self):self.invalid(lambda c:c['tests'][3].update(steady_last_s=100))
    def test_reviewed_standard_needs_reviewer(self):self.invalid(lambda c:c['standard'].update(reviewed=True))
    def test_unit_requires_string(self):self.invalid(lambda c:c['channels']['power'].update(unit=3))
    def test_valid_full_demo(self):validate(demo_suite())

class ReportTests(TempCase):
    def test_report_html_json_xml_exports(self):
        r,c=self.demo();res=run(r,c);paths=export(res,self.folder/'out');self.assertEqual(len(paths),4)
        self.assertIn('config_sha256',json.loads((self.folder/'out/result.json').read_text()))
        self.assertEqual(ET.fromstring(junit_text(res)).get('tests'),'6')
    def test_html_escapes_untrusted_content(self):
        r,c=self.demo();c['name']='<script>alert(1)</script>';c['tests'][0]['name']='<img src=x onerror=alert(1)>'
        text=html_report(run(r,c));self.assertNotIn('<script>alert(1)</script>',text);self.assertIn('&lt;script&gt;',text);self.assertNotIn('<img src=x',text)
    def test_csv_formula_injection(self):
        r,c=self.demo();c['tests'][0]['name']='=HYPERLINK("bad")';text=csv_text(run(r,c));self.assertIn("'=HYPERLINK",text)
    def test_junit_inconclusive_blocks_ci(self):
        r,c=self.demo('no_trigger');root=ET.fromstring(junit_text(run(r,c)));self.assertEqual(root.get('errors'),'6');self.assertEqual(len(root.findall('.//skipped')),0)
    def test_config_hash_stable_under_key_order(self):
        r,c=self.demo();h=run(r,c)['config_sha256'];c2=dict(reversed(list(c.items())));self.assertEqual(h,run(r,c2)['config_sha256'])
    def test_config_change_changes_hash(self):
        r,c=self.demo();h=run(r,c)['config_sha256'];c['name']+='changed';self.assertNotEqual(h,run(r,c)['config_sha256'])
    def test_trace_decimation_preserves_extreme(self):
        t=list(range(10000));y=[0]*10000;y[1234]=100;y[3001]=-100;trace=compact_trace(t,y,100);self.assertIn([1234.,100.],trace);self.assertIn([3001.,-100.],trace)
    def test_status_priority(self):self.assertEqual(overall([{'status':'FAIL'},{'status':'INCONCLUSIVE'}]),'FAIL');self.assertEqual(overall([{'status':'ERROR'},{'status':'FAIL'}]),'ERROR')
    def test_cli_pass_exit(self):
        r=subprocess.run([sys.executable,str(ROOT/'launch.py'),'run','--cfg',str(ROOT/'examples/01_pass_binary.cfg'),'--case',str(ROOT/'examples/01_pass_binary.json'),'--out',str(self.folder/'cli')],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
    def test_cli_fail_exit(self):
        r=subprocess.run([sys.executable,str(ROOT/'launch.py'),'run','--cfg',str(ROOT/'examples/06_overshoot.cfg'),'--case',str(ROOT/'examples/06_overshoot.json'),'--out',str(self.folder/'cli')],capture_output=True,text=True);self.assertEqual(r.returncode,1,r.stderr)
    def test_cli_inconclusive_exit(self):
        r=subprocess.run([sys.executable,str(ROOT/'launch.py'),'run','--cfg',str(ROOT/'examples/10_no_trigger.cfg'),'--case',str(ROOT/'examples/10_no_trigger.json'),'--out',str(self.folder/'cli')],capture_output=True,text=True);self.assertEqual(r.returncode,3,r.stderr)
    def test_cli_error_exit(self):
        r=subprocess.run([sys.executable,str(ROOT/'launch.py'),'run','--cfg',str(self.folder/'missing.cfg'),'--case',str(ROOT/'examples/01_pass_binary.json')],capture_output=True,text=True);self.assertEqual(r.returncode,2)

class ParameterizedTests(TempCase):pass

# Every format/revision combination is a separately counted test.
for revision,formats in [(1991,('ASCII','BINARY')),(1999,('ASCII','BINARY')),(2001,('ASCII','BINARY')),(2013,FORMATS)]:
    for fmt in formats:
        def make(revision,fmt):
            def test(self):
                t=[i*.001 for i in range(101)];y=[10+i*.02 for i in range(101)]
                r=self.record(fmt=fmt,revision=revision,times=t,ys=y)
                self.assertEqual(r.meta['revision'],revision)
                for a,b in zip(r.values['A1'],y):self.assertAlmostEqual(a,b,4)
                self.assertEqual(list(r.values['D1']),[int(.03<=x<.05) for x in t])
            return test
        setattr(ParameterizedTests,f'test_format_{revision}_{fmt.lower()}',make(revision,fmt))
        def make_missing(revision,fmt):
            def test(self):
                ys=[10.]*101;ys[50]=None;r=self.record(fmt=fmt,revision=revision,ys=ys)
                self.assertTrue(math.isnan(r.values['A1'][50]));self.assertEqual(run(r,cfg_for(range_test()))['status'],'INCONCLUSIVE')
            return test
        setattr(ParameterizedTests,f'test_missing_sentinel_{revision}_{fmt.lower()}',make_missing(revision,fmt))

# Analytic first-order response: t90=tau*ln(10), t10=-tau*ln(.9),
# rise=tau*ln(9). Independent formulas are the oracle, not the generator.
for seed in range(40):
    def make(seed):
        def test(self):
            rng=random.Random(seed);tau=rng.uniform(.02,.18);initial=rng.uniform(-20,20);delta=rng.choice([-1,1])*rng.uniform(30,80)
            dt=.0005;t=[i*dt for i in range(3601)];t0=.2
            y=[initial if x<t0 else initial+delta*(1-math.exp(-(x-t0)/tau)) for x in t]
            r=self.record(times=t,ys=y,fmt='FLOAT32',ach=[{'name':'A','unit':'V','a':1,'data':y}])
            a=step_test(initial+delta);a['normalization']='step';a['limits']={'response_s_max':.5};c=cfg_for(a)
            res=run(r,c);self.assertEqual(res['status'],'PASS');m=res['results'][0]['metrics']
            self.assertAlmostEqual(m['response_s'],tau*math.log(10),delta=dt)
            self.assertAlmostEqual(m['rise_s'],tau*math.log(9),delta=dt)
            self.assertLess(m['overshoot_pct'],1e-4)
            # Affine metamorphism preserves normalized dynamic metrics.
            factor=2.3;offset=-4;r.values['A1']=array('d',(factor*v+offset for v in r.values['A1']))
            c['tests'][0]['target']=factor*(initial+delta)+offset
            transformed=run(r,c)['results'][0]['metrics']
            self.assertAlmostEqual(transformed['response_s'],m['response_s'],9)
            self.assertAlmostEqual(transformed['steady_error_pct'],m['steady_error_pct'],6)
        return test
    setattr(ParameterizedTests,f'test_analytic_step_seed_{seed:03d}',make(seed))

# Random pulse width / polarity / format with exact sample-index oracle.
for seed in range(40):
    def make(seed):
        def test(self):
            rng=random.Random(1000+seed);start=rng.randint(10,30);width=rng.randint(3,40);active=rng.choice([0,1]);fmt=rng.choice(FORMATS)
            ds=[active if start<=i<start+width else 1-active for i in range(101)]
            r=self.record(fmt=fmt,ds=ds)
            a={'id':'pulse','type':'digital_pulse','channel':'d','window':[0,.1],'active':active,'min_width_s':width*.001,'max_width_s':width*.001}
            res=run(r,cfg_for(a));self.assertEqual(res['status'],'PASS');self.assertAlmostEqual(res['results'][0]['metrics']['widths_s'][0],width*.001,10)
            a['max_width_s']=width*.001-.0001;a['min_width_s']=0
            self.assertEqual(run(r,cfg_for(a))['status'],'FAIL')
        return test
    setattr(ParameterizedTests,f'test_random_pulse_seed_{seed:03d}',make(seed))

manifest=json.loads((ROOT/'examples/manifest.json').read_text(encoding='utf-8'))
for entry in manifest['cases']:
    def make(entry):
        def test(self):
            res=run(load(ROOT/'examples'/entry['cfg']),json.loads((ROOT/'examples'/entry['case']).read_text(encoding='utf-8')))
            self.assertEqual(res['status'],entry['expected'])
        return test
    setattr(ParameterizedTests,'test_scenario_'+Path(entry['cfg']).stem,make(entry))

if __name__=='__main__':unittest.main(verbosity=2)
