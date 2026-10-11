"""Deterministic adversarial noise fixtures. Expected outcomes are declared,
not derived from the engine. No field recordings; no national-standard limits.
"""
from __future__ import annotations
from copy import deepcopy
import json
import math
from pathlib import Path
import random
from .generate import write_record

CATALOG={
'N01_white_noise':'白噪声叠加工频纹波：健康响应',
'N02_emi_impulses':'EMI 孤立尖峰：原始峰值冲突待复核',
'N03_baseline_impulse':'基线孤立污染：稳健基线不被拉偏',
'N04_pwm_ripple':'开关纹波与量化噪声：固定容限',
'N05_colored_noise':'AR(1) 有色噪声：健康响应',
'N06_burst_noise':'成片强噪声：拒绝自动给出性能结论',
'N07_sustained_overshoot':'持续真实超调：不能被当作毛刺消除',
'N08_damped_ringing':'衰减振铃与噪声：正常恢复',
'N09_persistent_oscillation':'持续振荡：均值正确也不合格',
'N10_short_real_transient':'8 ms 真实短时超调：保留峰值',
'N11_raw_safety_spike':'单采样安全越限：原始硬限值否决',
'N12_ambiguous_spike':'同形短尖峰：不能仅凭外形区分噪声和故障',
'N13_slow_drift':'稳态缓慢漂移：禁止去趋势掩盖异常',
'N14_quantized_highrate':'5 kHz 量化噪声：按实际时间计算',
'N15_contact_bounce':'接点吸合及释放抖动：非对称去抖',
'N16_chatter_storm':'接点密集抖动风暴：质量预算拒绝',
'N17_protection_pulse':'1 ms 真实保护脉冲：原始通道保留',
'N18_invalid_debounce':'错误去抖配置：会吞掉合法保护脉冲',
'N19_active_low_bounce':'低有效脉冲：非对称吸合释放抖动',
'N20_unfinished_dwell':'窗尾去抖未完成：不能借用未来证据',
'N21_noisy_analog_trigger':'模拟触发噪声、提前尖峰和多次重穿越',
'N22_unqualified_trigger':'模拟触发只有短尖峰：无可信激励',
'N23_multirate_noise':'500/4000/1000 Hz 多采样率加噪',
'N24_timestamp_jitter':'采样时间抖动：用真实时间而非点数',
'N25_noise_with_gap':'噪声叠加数据间隙：禁止跨缺口平滑',
'N26_noise_with_missing':'噪声叠加关键缺失样本：禁止插补验收',
'N27_sensor_clipping':'传感器饱和削顶：测量失真待复核',
'N28_sensor_stuck':'反馈持续不响应：充分观察后失败',
'N29_negative_step':'充放电反向阶跃：负目标与噪声',
'N30_zero_target':'零目标阶跃：不使用目标值作分母',
'N31_timing_boundary':'性能限值附近：时间支持保护带复核',
'N32_many_channels':'16 模拟 + 512 数字通道：多通道噪声隔离',
}
EXPECTED={k:'PASS' for k in CATALOG}
for k in ('N02_emi_impulses','N06_burst_noise','N12_ambiguous_spike','N16_chatter_storm',
          'N20_unfinished_dwell','N22_unqualified_trigger','N25_noise_with_gap','N26_noise_with_missing',
          'N27_sensor_clipping','N31_timing_boundary'):EXPECTED[k]='INCONCLUSIVE'
for k in ('N07_sustained_overshoot','N09_persistent_oscillation','N10_short_real_transient',
          'N11_raw_safety_spike','N13_slow_drift','N17_protection_pulse','N28_sensor_stuck'):EXPECTED[k]='FAIL'
EXPECTED['N18_invalid_debounce']='ERROR'


def policy():
    return {'hampel_window_s':.012,'hampel_sigma':4.5,'hampel_floor':.9,'max_spike_s':.0031,
            'smooth_window_s':.003,'crossing_hold_s':.008,'crossing_hysteresis_fraction':.004,
            'settle_occupancy':.995,'max_excursion_s':.003,'max_replaced_fraction':.015,
            'max_noise_sigma':1.,'max_baseline_drift':1.,'max_steady_drift':1.2,
            'raw_min':-20.,'raw_max':160.}


def digital_policy():return {'rise_s':.003,'fall_s':.005,'max_rejected_edges':24,'max_chatter_s':.025,'merge_gap_s':.006,'timing_guard':True}


def base(name):
    return {'schema_version':1,'name':name,'description':'合成噪声工程回归；预期来自注入模型与显式限值，不是厂家现场验证。',
            'standard':{'reference':'噪声算法工程示例；非国标条款','reviewed':False,'note':'所有容限必须由项目审核后替换。'},
            'channels':{},'tests':[]}


def step(target=100.):
    return {'id':'ROBUST_STEP','name':'抗噪性能 + 原始硬限值 + 质量预算','type':'analog_step','channel':'p',
            'trigger':{'type':'time','at':.25},'window':[0,1.75],'baseline_window':[-.2,-.03],
            'target':target,'normalization':'step','steady_last_s':.5,'settle_band_pct':2.,'settle_hold_s':.15,
            'limits':{'overshoot_pct_max':5.,'response_s_max':.28,'rise_s_max':.23,'latency_s_max':.08,
                      'settling_s_max':.6,'steady_error_pct_max':.8,'steady_ripple_pp_max':3.},'robust':policy()}


def analog_case(case_id,seed):
    rng=random.Random(seed);rate=5000 if case_id=='N14_quantized_highrate' else (2000 if case_id=='N32_many_channels' else 1000)
    t=[i/rate for i in range(2*rate+1)];kw={};injections=[]
    if case_id=='N23_multirate_noise':
        t=[i/500 for i in range(151)]+[.3+i/4000 for i in range(1,2001)]+[.8+i/1000 for i in range(1,1201)]
        kw['rates']=[(500,151),(4000,2151),(1000,len(t))]
    elif case_id=='N24_timestamp_jitter':
        t=[0.]+[(i+rng.uniform(-.12,.12))/1000 for i in range(1,2000)]+[2.]
        kw['rates']=[(1000,len(t))]
    elif case_id=='N25_noise_with_gap':t=[x for x in t if not .9<x<.911]
    baseline,target=(100.,-100.) if case_id=='N29_negative_step' else ((-80.,0.) if case_id=='N30_zero_target' else (0.,100.))
    def ideal(x):return baseline if x<.28 else target+(baseline-target)*math.exp(-(x-.28)/.075)
    ideal_y=[ideal(x) for x in t];sigma=.15 if target==0 else .25;y=[];colored=0.
    for x,value in zip(t,ideal_y):
        noise=rng.gauss(0,sigma)+.12*math.sin(2*math.pi*50*x)
        if case_id=='N05_colored_noise':colored=.9*colored+rng.gauss(0,.06);noise=colored
        if case_id=='N04_pwm_ripple':noise=1.5*math.sin(2*math.pi*400*x)+rng.gauss(0,.15)
        if case_id=='N06_burst_noise' and (.05<x<.22 or 1.5<x<2):noise+=rng.gauss(0,5.)
        if case_id=='N09_persistent_oscillation' and x>=.6:noise+=6*math.sin(2*math.pi*8*x)
        if case_id=='N08_damped_ringing' and x>=.6:noise+=3.5*math.exp(-(x-.6)/.15)*math.sin(2*math.pi*18*(x-.6))
        value+=noise
        if case_id=='N07_sustained_overshoot' and .5<=x<.62:value=125.+noise
        if case_id=='N10_short_real_transient' and .8<=x<.808:value=125.+noise
        if case_id=='N13_slow_drift' and x>=1.2:value+=10*(x-1.2)
        if case_id=='N27_sensor_clipping':value=min(85.,value)
        if case_id=='N28_sensor_stuck':value=0.
        if case_id=='N14_quantized_highrate':value=round(value/.1)*.1
        y.append(value)
    def spike(at,amount,count=1):
        i=min(range(len(t)),key=lambda i:abs(t[i]-at))
        for j in range(i,min(len(y),i+count)):y[j]+=amount
        injections.append({'type':'injected_impulse','start_s':t[i],'samples':count,'amplitude':amount})
    if case_id=='N02_emi_impulses':spike(.36,65);spike(.83,24,2);spike(1.1,-18)
    if case_id=='N03_baseline_impulse':spike(.12,-8)
    if case_id in ('N11_raw_safety_spike','N12_ambiguous_spike'):spike(.8,25.)
    if case_id=='N32_many_channels':spike(.7,2.)
    if case_id=='N26_noise_with_missing':y[min(range(len(t)),key=lambda i:abs(t[i]-1.65))]=None
    c=base(CATALOG[case_id]);a=step(target);r=a['robust']
    if baseline==100:r.update(raw_min=-160,raw_max=160)
    if baseline==-80:r.update(raw_min=-100,raw_max=20)
    if case_id=='N04_pwm_ripple':r['max_noise_sigma']=2.;a['limits']['steady_ripple_pp_max']=5.
    if case_id=='N09_persistent_oscillation':r.update(max_noise_sigma=10,max_steady_drift=20)
    if case_id=='N11_raw_safety_spike':r['raw_max']=120.
    if case_id=='N27_sensor_clipping':r.update(clip_min=-20.,clip_max=85.,max_clip_fraction=.02)
    if case_id=='N31_timing_boundary':a['limits']['response_s_max']=.03+.075*math.log(10)
    aa=[{'name':'PCS有功功率_加噪','unit':'kW','a':.001,'data':y}]
    dd=[{'name':'调度激励','data':[int(x>=.25) for x in t]}]
    c['channels']={'p':{'kind':'analog','index':1,'unit':'kW'}};c['tests']=[a]
    expected={'ROBUST_STEP':EXPECTED[case_id]}
    if case_id=='N13_slow_drift':
        expected['ROBUST_STEP']='INCONCLUSIVE'
        c['tests'].append({'id':'RAW_DRIFT','name':'原始稳态范围仍检出漂移','type':'analog_range','channel':'p','window':[1.5,2.], 'min':99.,'max':103.})
        expected['RAW_DRIFT']='FAIL'
    if case_id=='N32_many_channels':
        for k in range(15):aa.append({'name':f'背景测量_{k+2:02d}','unit':'V','a':.01,'data':[700+rng.gauss(0,2) for _ in t]})
        for k in range(511):dd.append({'name':f'备用数字量_{k+2:03d}','data':[int((j//(k+5))%2) for j in range(len(t))]})
    oracle={}
    if case_id in ('N01_white_noise','N05_colored_noise','N14_quantized_highrate','N23_multirate_noise','N24_timestamp_jitter','N29_negative_step','N30_zero_target','N32_many_channels'):
        oracle={'ROBUST_STEP':{'response_s':[.03+.075*math.log(10)-.012,.03+.075*math.log(10)+.012],
                                'rise_s':[.075*math.log(9)-.015,.075*math.log(9)+.015]}}
    return t,aa,dd,c,expected,kw,{'injections':injections,'metric_oracles':oracle,'ideal_response_s':.03+.075*math.log(10),'ideal_rise_s':.075*math.log(9)}


def digital_case(case_id,seed):
    rng=random.Random(seed);t=[i/1000 for i in range(1001)];nr=digital_policy();c=base(CATALOG[case_id]);expected={}
    active=0 if case_id=='N19_active_low_bounce' else 1
    intervals=[(.5,.501),(.503,.504),(.509,.7),(.701,.703)]
    if case_id=='N19_active_low_bounce':intervals=[(.5,.501),(.503,.504),(.507,.55),(.552,.554)];nr.update(rise_s=.006,fall_s=.002)
    if case_id=='N16_chatter_storm':intervals=[(i/1000,(i+1)/1000) for i in range(400,560,2)]
    if case_id in ('N17_protection_pulse','N18_invalid_debounce'):intervals=[(.5,.501)]
    if case_id=='N20_unfinished_dwell':intervals=[(.998,1.1)]
    y=[active if any(a-1e-12<=x<b-1e-12 for a,b in intervals) else 1-active for x in t]
    dd=[{'name':'接点反馈','data':y}];aa=[{'name':'母线背景噪声','unit':'V','a':.001,'data':[700+rng.gauss(0,.3) for _ in t]}]
    c['channels']={'d':{'kind':'digital','index':1}}
    if case_id in ('N15_contact_bounce','N19_active_low_bounce'):
        width=(.19,.2) if active else (.044,.05)
        c['tests']=[{'id':'QUALIFIED_PULSE','name':'有效脉冲与去抖审计','type':'digital_pulse','channel':'d','window':[.4,.85],
                     'active':active,'count':1,'min_width_s':width[0],'max_width_s':width[1],'noise':nr}]
        expected={'QUALIFIED_PULSE':'PASS'}
    elif case_id in ('N16_chatter_storm','N20_unfinished_dwell'):
        c['tests']=[{'id':'QUALIFIED_HOLD','type':'digital_hold','channel':'d','window':[.35,1.], 'value':0,'noise':nr}]
        expected={'QUALIFIED_HOLD':'INCONCLUSIVE'}
    else:
        c['tests']=[{'id':'RAW_NO_TRIP','name':'原始保护通道禁止任何跳闸','type':'digital_hold','channel':'d','window':[.4,.6],'value':0},
                    {'id':'RAW_PULSE','name':'观察到的真实 1 ms 保护脉冲','type':'digital_pulse','channel':'d','window':[.4,.6],
                     'active':1,'count':1,'min_width_s':.0005,'max_width_s':.0015}]
        expected={'RAW_NO_TRIP':'FAIL','RAW_PULSE':'PASS'}
        if case_id=='N18_invalid_debounce':c['tests'][1]['noise']=nr;expected={'CONFIGURATION':'ERROR'}
    return t,aa,dd,c,expected,{}, {'injections':intervals,'metric_oracles':{}}


def trigger_case(case_id,seed):
    rng=random.Random(seed);t=[i/2000 for i in range(2001)];y=[]
    for x in t:
        v=rng.gauss(0,.02)
        if .2<=x<.4:v=.5+.10*math.sin(2*math.pi*120*x)+rng.gauss(0,.01)
        if .35<=x<.351 or .42<=x<.422:v=1.
        if case_id=='N21_noisy_analog_trigger' and x>=.5:
            v=1.+rng.gauss(0,.015)
            if .501<=x<.503 or .504<=x<.507:v=0.
        y.append(v)
    aa=[{'name':'含噪声模拟激励','unit':'V','a':.0001,'data':y}]
    dd=[{'name':'动作反馈','data':[int(x>=.525) for x in t]}]
    c=base(CATALOG[case_id]);c['channels']={'cmd':{'kind':'analog','index':1},'ack':{'kind':'digital','index':1}}
    c['tests']=[{'id':'ACK_AFTER_QUALIFIED_TRIGGER','name':'经过滞回与持续确认的动作时延','type':'digital_edge','channel':'ack',
                'trigger':{'type':'analog_crossing','channel':'cmd','threshold':.5,'hysteresis':.15,'hold_s':.006,'edge':'rising','search':[.1,.6]},
                'window':[0,.08],'edge':'rising','count':1,'latency_min_s':.01,'latency_max_s':.03,'noise':digital_policy()}]
    return t,aa,dd,c,{'ACK_AFTER_QUALIFIED_TRIGGER':EXPECTED[case_id]}, {},{'injections':[],'metric_oracles':{}}


def generate_case(folder,case_id,fmt='FLOAT32',seed=20261011):
    if case_id not in CATALOG:raise ValueError('未知噪声工况')
    if case_id in ('N21_noisy_analog_trigger','N22_unqualified_trigger'):data=trigger_case(case_id,seed)
    elif 15<=int(case_id[1:3])<=20:data=digital_case(case_id,seed)
    else:data=analog_case(case_id,seed)
    t,aa,dd,c,expected,kw,truth=data
    if fmt in ('BINARY','ASCII'):
        for ch in aa:
            ch['a']=max(.001,max((abs(v) for v in ch['data'] if v is not None),default=1)/30000)
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    cfg=write_record(folder,case_id,t,aa,dd,fmt=fmt,revision=2013,trigger_s=.25,**kw)
    cp=folder/(case_id+'.json');cp.write_text(json.dumps(c,ensure_ascii=False,indent=2),encoding='utf-8')
    entry={'id':case_id,'name':CATALOG[case_id],'cfg':cfg.name,'case':cp.name,'expected':EXPECTED[case_id],
           'expected_assertions':expected,'seed':seed,'format':fmt,'samples':len(t),'analog_channels':len(aa),'digital_channels':len(dd),
           'truth_model':truth,'metric_oracles':truth.get('metric_oracles',{})}
    return cfg,cp,entry


def generate_noise(folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    entries=[generate_case(folder,k)[2] for k in CATALOG]
    data={'name':'v1.2 噪声与扰动回归','notice':'全部为合成注入；预期包括 FAIL、INCONCLUSIVE 和 ERROR。不是现场故障检出率。','cases':entries}
    (folder/'manifest.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');return data
