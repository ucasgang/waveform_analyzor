"""Composite synthetic FAT cases; all thresholds are illustrative, not standards.
Expected verdicts are specified at assertion level before calling the analyzer.
Signals combine physical-time responses, unrelated channels and protection logic.
"""
from __future__ import annotations
import copy,json,math,random
from pathlib import Path
from .generate import write_record

CATALOG={
 'C01_cycle_pass':'充电—放电—归零：三段闭环与反馈握手',
 'C02_cycle_race':'第二段反馈迟到 + 预充/合闸次序颠倒',
 'C03_cycle_oscillation':'中段反复出带，均值正常但调节失败',
 'C04_sag_pass':'三相跌落、谐波、闭锁与恢复联动',
 'C05_sag_trip':'跌落中插入 1 ms 非预期跳闸',
 'C06_multirate_skew':'三段采样率 + 750 us 偏移 + 变比/单位换算',
 'C07_fault_reset_pass':'两次保护动作、复归及功率归零',
 'C08_fault_reset_race':'故障尚未复归就提前发出就绪反馈',
 'C09_dense_512':'512 通道 + 单采样尖峰 + 过密数字变位',
 'C10_target_drift':'第二段目标窗口漂移，不能定义恒定目标',
 'C11_selected_missing':'三相跌落中单相关键窗口缺失样本',
 'C12_causal_debounce':'末端去抖未结束，后续复位不能倒推通过',
}


def base(name):
    return {'schema_version':1,'name':name,'description':'复合合成工况；每项预期结论独立列于 manifest。',
            'standard':{'reference':'工程回归 / 非国标限值','clause':'合成数据验证','reviewed':False},'channels':{},'tests':[]}


def add(c,expected,ident,typ,ch,window,*,outcome='PASS',**kw):
    c['tests'].append({'id':ident,'name':kw.pop('name',ident),'type':typ,'channel':ch,'window':window,**kw})
    expected[ident]=outcome


def mapping(c,aa,dd):
    for ch in aa:c['channels'][ch['alias']]={'kind':'analog','name':ch['name'],'unit':ch['unit']}
    for ch in dd:c['channels'][ch['alias']]={'kind':'digital','name':ch['name']}


def analog(alias,name,data,unit='kW',**kw):return {'alias':alias,'name':name,'data':data,'unit':unit,**kw}
def digital(alias,name,data,**kw):return {'alias':alias,'name':name,'data':data,**kw}
def pulse(t,windows,active=1):return [active if any(a-1e-10<=x<b-1e-10 for a,b in windows) else 1-active for x in t]
def tr(alias,occurrence=1,search=None,edge='rising',debounce=.002):
    d={'type':'digital_edge','channel':alias,'edge':edge,'occurrence':occurrence,'debounce_s':debounce}
    if search is not None:d['search']=search
    return d

def step_rule(c,expected,id,channel,trigger,window,target=None,**kw):
    common={'trigger':trigger,'baseline_window':[-.18,-.03],'normalization':'rated','rated':100,
            'steady_last_s':.25,'settle_band_pct':2,'settle_hold_s':.1,
            'limits':{'overshoot_pct_max':3,'response_s_max':.35,'rise_s_max':.3,'settling_s_max':.6,'steady_error_pct_max':.5,'steady_ripple_pp_max':1}}
    if target is not None:common['target']=target
    common.update(kw);add(c,expected,id,'analog_step',channel,window,**common)


def cycle(case_id,seed=2718):
    rng=random.Random(seed);t=[i/1000 for i in range(10001)];events=[.5,3.3,6.2];targets=[100.,-80.,0.]
    p=[];setpoint=[]
    for x in t:
        phase=max((j for j,e in enumerate(events) if x>=e-1e-10),default=-1)
        setpoint.append(targets[phase] if phase>=0 else 0.)
        j=max((j for j,e in enumerate(events) if x>=e+.025-1e-10),default=-1)
        y=0. if j<0 else targets[j]+((0. if j==0 else targets[j-1])-targets[j])*math.exp(-(x-events[j]-.025)/(.065 if j!=1 else .08))
        if case_id=='C03_cycle_oscillation' and 4.0<=x<5.9:y+=4*math.sin(2*math.pi*8*(x-4.0))
        p.append(y+rng.uniform(-.018,.018))
    if case_id=='C10_target_drift':
        for i,x in enumerate(t):
            if 3.32<=x<=3.38:setpoint[i]+=(x-3.32)*30
    ack=[(e+(.125 if j==1 and case_id=='C02_cycle_race' else .035),e+(.16 if j==1 and case_id=='C02_cycle_race' else .07)) for j,e in enumerate(events)]
    # A rejected 1 ms bounce precedes each true acknowledged pulse.
    ack_bounces=[(e+.028,e+.029) for e in events]
    aa=[analog('power','PCS有功功率',p),analog('target','有功设定值',setpoint),
        analog('dc','直流母线',[700+1.2*math.sin(2*math.pi*100*x) for x in t],'V')]
    aa += [analog(f'aux{i}',f'非目标测点_{i:02d}',[20+i+math.sin(x*(i+1)) for x in t],'V') for i in range(9)]
    dd=[digital('cmd','调度命令脉冲',pulse(t,[(e,e+.02) for e in events])),digital('ack','命令确认反馈',pulse(t,ack+ack_bounces)),
        digital('precharge','预充脉冲',pulse(t,[(.53,.61)])),digital('pre_done','预充完成',[int(x>=.64) for x in t]),
        digital('closed','主接触器闭合',[int(x>=(.62 if case_id=='C02_cycle_race' else .67)) for x in t]),
        digital('alarm','故障锁存',[0]*len(t)),digital('reset_low','低有效复归脉冲',pulse(t,[(8.2,8.23),(8.5,8.53)],active=0)),
        digital('heartbeat','心跳',[(i//25)%2 for i in range(len(t))])]
    dd += [digital(f'auxd{i}',f'辅助接点_{i:02d}',[int((k//(50+i*5))%2) for k in range(len(t))]) for i in range(24)]
    c=base(CATALOG[case_id]);ex={};mapping(c,aa,dd)
    # Only business channels are mapped; auxiliary channels remain distractions.
    c['channels']={k:v for k,v in c['channels'].items() if not k.startswith('aux')}
    for j,(event,target) in enumerate(zip(events,targets),1):
        trigger=tr('cmd',j,[0,9.8])
        outcome='INCONCLUSIVE' if j==2 and case_id=='C10_target_drift' else ('FAIL' if j==2 and case_id=='C03_cycle_oscillation' else 'PASS')
        step_rule(c,ex,f'P{j}','power',trigger,[0,2.5],outcome=outcome,target_channel='target',target_window=[.02,.06],target_tolerance=.01)
        add(c,ex,f'ACK{j}','digital_edge','ack',[0,.23],trigger=trigger,edge='rising',count=1,debounce_s=.004,latency_min_s=.02,latency_max_s=.06,
            outcome='FAIL' if j==2 and case_id=='C02_cycle_race' else 'PASS')
        add(c,ex,f'PULSE{j}','digital_pulse','ack',[0,.23],trigger=trigger,active=1,count=1,debounce_s=.004,min_width_s=.034,max_width_s=.036)
        add(c,ex,f'DC{j}','analog_range','dc',[0,2.5],trigger=trigger,min=696,max=704)
    add(c,ex,'CMD_COUNT','digital_edge','cmd',[0,9.8],edge='rising',count=3,debounce_s=.003)
    add(c,ex,'PRECHARGE_WIDTH','digital_pulse','precharge',[.45,.7],active=1,count=1,min_width_s=.078,max_width_s=.082)
    add(c,ex,'CLOSE_ORDER','digital_edge','closed',[-.08,.12],trigger=tr('pre_done',search=[.4,.8]),edge='rising',count=1,latency_min_s=.01,latency_max_s=.05,
        outcome='FAIL' if case_id=='C02_cycle_race' else 'PASS')
    add(c,ex,'NO_ALARM','digital_hold','alarm',[0,9.8],value=0)
    add(c,ex,'LOW_RESET','digital_pulse','reset_low',[8,8.8],active=0,count=2,min_width_s=.029,max_width_s=.031)
    # Inclusive end falls in inactive state; 20 full 25 ms pulses.
    add(c,ex,'HEARTBEAT','digital_pulse','heartbeat',[1,2],active=1,count=20,min_width_s=.024,max_width_s=.026)
    return t,aa,dd,c,ex,{},{}


def sag(case_id,seed=2718):
    t=[i/10000 for i in range(25001)]
    aa=[]
    for k,phase in enumerate((0,-2*math.pi/3,2*math.pi/3)):
        data=[]
        for x in t:
            m=.2 if .65<=x<.95 else 1
            data.append(230*math.sqrt(2)*m*(math.sin(2*math.pi*50*x+phase)+.03*math.sin(2*math.pi*250*x+5*phase)))
        if case_id=='C11_selected_missing' and k==1:data[8020]=None
        aa.append(analog(f'u{k}',f'相电压{"ABC"[k]}',data,'V',a=.1))
    aa.append(analog('envelope','电压包络指示_合成',[46 if .65<=x<.95 else 230 for x in t],'V'))
    power=[100 if x<.65 else (20+80*math.exp(-(x-.65)/.008) if x<.95 else 100-80*math.exp(-(x-.95)/.07)) for x in t]
    aa.append(analog('power','PCS有功功率',power))
    aa.append(analog('unused','非目标缺失测点',[None]*len(t),'V'))
    dd=[digital('block','低电压闭锁',pulse(t,[(.656,.954)])),digital('trip','跳闸反馈',pulse(t,[(.79,.791)]) if case_id=='C05_sag_trip' else [0]*len(t)),
        digital('connected','并网接触器',[1]*len(t)),digital('alarm_low','低有效告警脉冲',pulse(t,[(.66,.69)],0))]
    dd += [digital(f'aux{i}',f'辅助状态_{i}',[0]*len(t)) for i in range(16)]
    c=base(CATALOG[case_id]);ex={};mapping(c,aa,dd);c['channels']={k:v for k,v in c['channels'].items() if k!='unused' and not k.startswith('aux')}
    for k in range(3):
        for tag,window,limits in [('PRE',[.35,.55],[226,234]),('SAG',[.7,.9],[45,48]),('POST',[1.4,1.6],[226,234])]:
            add(c,ex,f'RMS_{k}_{tag}','analog_rms',f'u{k}',window,min=limits[0],max=limits[1],outcome='INCONCLUSIVE' if case_id=='C11_selected_missing' and k==1 and tag=='SAG' else 'PASS')
    add(c,ex,'BLOCK_WIDTH','digital_pulse','block',[.6,1.1],active=1,count=1,min_width_s=.297,max_width_s=.299)
    add(c,ex,'NO_TRIP','digital_hold','trip',[.6,1.1],value=0,outcome='FAIL' if case_id=='C05_sag_trip' else 'PASS')
    add(c,ex,'KEEP_GRID','digital_hold','connected',[.6,1.1],value=1)
    add(c,ex,'LOW_ALARM','digital_pulse','alarm_low',[.6,1.1],active=0,count=1,min_width_s=.029,max_width_s=.031)
    trigger={'type':'analog_crossing','channel':'envelope','threshold':150,'edge':'rising','search':[.9,1.1]}
    step_rule(c,ex,'P_RECOVERY','power',trigger,[0,1.2],target=100)
    return t,aa,dd,c,ex,{},{}


def multirate(case_id,seed=2718):
    t=[i*.002 for i in range(301)]+[.6+i*.00025 for i in range(1,2401)]+[1.2+i*.001 for i in range(1,2301)]
    rates=[(500,301),(4000,2701),(1000,len(t))];skew=.00075
    def response(x):return 0 if x<.74 else 100*(1-math.exp(-(x-.74)/.075))
    aa=[analog('power','功率二次测量',[100*response(x+skew) for x in t],'W',a=-.1,b=123,skew_us=750,primary=1000,secondary=100,side='S'),
        analog('target','功率一次指令',[0 if x<.7 else 100000 for x in t],'W',a=1),
        analog('unused','未使用传感器',[None]*len(t),'V')]
    dd=[digital('cmd','启动激励',pulse(t,[(.7,.72)])),digital('ack','完成反馈',pulse(t,[(.725,.726),(.73,1.4)]))]
    c=base(CATALOG[case_id]);ex={};mapping(c,aa,dd);del c['channels']['unused']
    c['channels']['power'].update(side='primary',scale=.001,output_unit='kW');c['channels']['target'].update(scale=.001,output_unit='kW')
    step_rule(c,ex,'SKEW_STEP','power',tr('cmd',search=[.6,.9]),[0,2],target_channel='target',target_window=[.03,.1],target_tolerance=0)
    add(c,ex,'BOUNCED_ACK','digital_edge','ack',[0,.2],trigger=tr('cmd',search=[.6,.9]),count=1,edge='rising',debounce_s=.003,latency_min_s=.029,latency_max_s=.031)
    add(c,ex,'POST_BAND','analog_range','power',[1.5,2.5],min=99.9,max=100.1)
    oracle={'SKEW_STEP':{'response_s':[.04+.075*math.log(10)-.0004,.04+.075*math.log(10)+.0004],
                         'rise_s':[.075*math.log(9)-.0004,.075*math.log(9)+.0004]},'BOUNCED_ACK':{'edge_count':[1,1]}}
    return t,aa,dd,c,ex,{'rates':rates},oracle


def fault_reset(case_id,seed=2718):
    t=[i/2000 for i in range(6001)]
    power=[]
    for x in t:
        if x<.504:y=100
        elif x<1.55:y=100*math.exp(-(x-.504)/.008)
        elif x<2.004:y=100*(1-math.exp(-(x-1.55)/.025))
        else:y=100*math.exp(-(x-2.004)/.008)
        power.append(y)
    ready_at=1.51 if case_id=='C08_fault_reset_race' else 1.54
    aa=[analog('power','PCS功率',power),analog('dc','直流电压',[700-15*math.sin(2*math.pi*3*x) for x in t],'V')]
    dd=[digital('fault','故障注入',pulse(t,[(.5,.55),(2,2.05)])),digital('block','门极闭锁',pulse(t,[(.503,1.535),(2.003,3.1)])),
        digital('trip_low','低有效跳闸脉冲',pulse(t,[(.52,.56),(2.02,2.06)],0)),
        digital('latch','故障锁存',pulse(t,[(.505,1.525),(2.005,3.1)])),digital('reset','复归命令',pulse(t,[(1.5,1.52)])),
        digital('ready','运行就绪',[int(x<.507 or ready_at<=x<2.007) for x in t]),
        digital('closed','主回路闭合',[int(x<.518 or 1.56<=x<2.018) for x in t])]
    dd += [digital(f'aux{i}',f'备用接点_{i:02d}',[0]*len(t)) for i in range(33)]
    c=base(CATALOG[case_id]);ex={};mapping(c,aa,dd);c['channels']={k:v for k,v in c['channels'].items() if not k.startswith('aux')}
    for j,e in enumerate((.5,2.),1):
        trigger=tr('fault',j,[.4,2.5],debounce=.001)
        add(c,ex,f'BLOCK_{j}','digital_edge','block',[0,.05],trigger=trigger,count=1,edge='rising',latency_min_s=.002,latency_max_s=.004)
        add(c,ex,f'OPEN_{j}','digital_edge','closed',[0,.08],trigger=trigger,count=1,edge='falling',latency_min_s=.01,latency_max_s=.03)
        add(c,ex,f'TRIP_{j}','digital_pulse','trip_low',[0,.1],trigger=trigger,active=0,count=1,min_width_s=.039,max_width_s=.041)
        add(c,ex,f'LATCH_{j}','digital_hold','latch',[.04,.4],trigger=trigger,value=1)
        add(c,ex,f'NOT_READY_{j}','digital_hold','ready',[.04,.4],trigger=trigger,value=0)
        step_rule(c,ex,f'POWER_ZERO_{j}','power',trigger,[0,.6],target=0,baseline_window=[-.08,-.015],steady_last_s=.1,
            limits={'response_s_max':.04,'settling_s_max':.08,'overshoot_pct_max':1,'steady_error_pct_max':.1})
    add(c,ex,'CLEAR_LATCH','digital_edge','latch',[0,.1],trigger=tr('reset',search=[1.4,1.7]),edge='falling',count=1,latency_min_s=.02,latency_max_s=.03)
    add(c,ex,'READY_AFTER_CLEAR','digital_edge','ready',[-.04,.08],trigger=tr('latch',search=[1.4,1.7],edge='falling'),edge='rising',count=1,latency_min_s=.005,latency_max_s=.03,
        outcome='FAIL' if case_id=='C08_fault_reset_race' else 'PASS')
    add(c,ex,'BUS_RANGE','analog_range','dc',[.1,2.9],min=680,max=720)
    return t,aa,dd,c,ex,{},{}


def dense(case_id,seed=2718):
    t=[i/10000 for i in range(20001)];n=len(t)
    aa=[analog(f'a{i+1}',f'测量_{i+1:03d}',[float(100+i) if i!=127 else (125. if j==12345 else 100.) for j in range(n)],'V',a=.01) for i in range(128)]
    dd=[digital(f'd{i+1}',f'开关_{i+1:03d}',[0]*n) for i in range(384)]
    for k in (16,17,32,33,255,256,257):dd[k-1]['data']=pulse(t,[(.8,.8+.0001*k)])
    dd[299]['data']=[i%2 for i in range(n)];dd[383]['data']=[int(i==12345) for i in range(n)]
    c=base(CATALOG[case_id]);ex={};mapping(c,aa,dd)
    keep={'a1','a128','d300','d384'}|{f'd{k}' for k in (16,17,32,33,255,256,257)};c['channels']={k:v for k,v in c['channels'].items() if k in keep}
    add(c,ex,'SINGLE_SPIKE','analog_range','a128',[1.2,1.3],min=99,max=103,outcome='FAIL')
    add(c,ex,'OTHER_CHANNEL','analog_range','a1',[0,2],min=100,max=100)
    add(c,ex,'SINGLE_SAMPLE_PULSE','digital_pulse','d384',[1.2,1.3],active=1,count=1,min_width_s=.00009,max_width_s=.00011)
    for k in (16,17,32,33,255,256,257):
        add(c,ex,f'WORD_BIT_{k}','digital_pulse',f'd{k}',[.7,1],active=1,count=1,min_width_s=k*.0001-.00001,max_width_s=k*.0001+.00001)
    add(c,ex,'HIGH_FREQUENCY','digital_pulse','d300',[0,2],active=1,count=10000,min_width_s=.00009,max_width_s=.00011)
    oracle={'SINGLE_SPIKE':{'max':[125,125]},'SINGLE_SAMPLE_PULSE':{'pulse_count':[1,1]}}
    return t,aa,dd,c,ex,{},oracle


def causal(case_id,seed=2718):
    t=[i/10000 for i in range(2001)]
    aa=[analog('dc','直流母线',[700+math.sin(100*x) for x in t],'V')]
    dd=[digital('alarm','抖动告警',pulse(t,[(.098,.102)])),digital('cmd','启动指令',pulse(t,[(.03,.04)])),digital('ack','启动反馈',pulse(t,[(.035,.08)]))]
    c=base(CATALOG[case_id]);ex={};mapping(c,aa,dd)
    add(c,ex,'END_UNRESOLVED','digital_hold','alarm',[0,.1],value=0,debounce_s=.005,outcome='INCONCLUSIVE')
    add(c,ex,'RAW_GLITCH','digital_hold','alarm',[0,.11],value=0,outcome='FAIL')
    add(c,ex,'FULLY_OBSERVED_FILTER','digital_hold','alarm',[0,.11],value=0,debounce_s=.005)
    add(c,ex,'ACK','digital_edge','ack',[0,.06],trigger=tr('cmd',search=[0,.06]),edge='rising',count=1,latency_min_s=.004,latency_max_s=.006)
    add(c,ex,'DC','analog_range','dc',[0,.1],min=698,max=702)
    return t,aa,dd,c,ex,{},{}


def generate_case(folder,case_id,fmt=None,seed=2718):
    if case_id not in CATALOG:raise ValueError('未知复杂工况')
    if case_id in ('C01_cycle_pass','C02_cycle_race','C03_cycle_oscillation','C10_target_drift'):data=cycle(case_id,seed)
    elif case_id in ('C04_sag_pass','C05_sag_trip','C11_selected_missing'):data=sag(case_id,seed)
    elif case_id=='C06_multirate_skew':data=multirate(case_id,seed)
    elif case_id in ('C07_fault_reset_pass','C08_fault_reset_race'):data=fault_reset(case_id,seed)
    elif case_id=='C09_dense_512':data=dense(case_id,seed)
    else:data=causal(case_id,seed)
    t,aa,dd,c,expected,kw,oracle=data
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    fmt=fmt or ('BINARY32' if case_id in ('C06_multirate_skew','C09_dense_512') else 'FLOAT32')
    if fmt=='BINARY':
        # Different encoding quantization, not different physics or assertions.
        for ch in aa:ch['a']=max(ch.get('a',.01),max((abs(v-ch.get('b',0)) for v in ch['data'] if v is not None),default=0)/30000,.01)
    cfg=write_record(folder,case_id,t,aa,dd,fmt=fmt,revision=2013,trigger_s=.5,**kw)
    cp=folder/(case_id+'.json');cp.write_text(json.dumps(c,ensure_ascii=False,indent=2),encoding='utf-8')
    status=next((s for s in ('ERROR','FAIL','INCONCLUSIVE') if s in expected.values()),'PASS')
    entry={'id':case_id,'name':CATALOG[case_id],'cfg':cfg.name,'case':cp.name,'expected':status,'expected_assertions':expected,
           'metric_oracles':oracle,'samples':len(t),'analog_channels':len(aa),'digital_channels':len(dd),'format':fmt,'seed':seed}
    return cfg,cp,entry


def generate_complex(folder):
    folder=Path(folder);cases=[]
    for case_id in CATALOG:
        _,_,entry=generate_case(folder,case_id);cases.append(entry)
    out={'name':'v1.1 复杂联动工况回归','notice':'阈值为教学配置。匹配预期包括正确检出 FAIL 和 INCONCLUSIVE；不表示被测设备全部合格。','cases':cases}
    (folder/'manifest.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');return out
