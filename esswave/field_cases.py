"""Physics-inspired, REDUCED-ORDER synthetic FAT scene family (not field captures).
Two PCS state models -> current priority -> DC energy/battery RC -> sensor chain
-> converter quantization -> COMTRADE. Injection truth is saved separately and
is NEVER an input to the verdict engine. No EMT/HIL/standard fidelity claim.
"""
from __future__ import annotations
import copy, hashlib, json, math, random
from bisect import bisect_right
from pathlib import Path
from .generate import write_record
from .complex_cases import base,add,mapping,analog,digital

CATALOG={
'F01_coupled_nominal':'双PCS充放电切换 + 三相畸变 + 直流能量耦合',
'F02_dispatch_delay':'PCS2命令滞后：总功率出现两阶段响应',
'F03_low_damping':'低阻尼闭环与相关测量噪声：真实超调',
'F04_deep_sag':'深度不平衡跌落 + 无功优先 + 电流限幅恢复',
'F05_trip_1ms':'电网跌落期间1 ms原始保护脉冲',
'F06_stale_telemetry':'总表遥测冻结240 ms，与分机功率不一致',
'F07_ct_vt_clipping':'B相电压测量削顶，冗余测量仍正常',
'F08_common_mode_emi':'多通道同步共模双点尖峰：不能确认噪声归因',
'F09_clock_jitter':'非均匀采样抖动 + AR噪声 + 量化阶梯',
'F10_multirate_skew':'跌落段升采样率 + 异步通道skew + 增益偏置',
'F11_acquisition_gap':'关键跌落窗口缺失20 ms数据块',
'F12_selected_dropout':'只有PCS2关键采样失效，其他通道保持正常',
'F13_sensor_gain_drift':'单表增益缓慢漂移：总表与分表矛盾',
'F14_handshake_loss':'接点正常抖动背景下丢失第二次确认',
'F15_limiter_disabled':'限流环失效：跌落期间真实过流',
'F16_compound_faults':'通信冻结 + 失去确认 + 保护短脉冲 + 同步尖峰',
'F17_512_channel_rack':'128模拟 + 384数字：电池簇与多设备干扰背景',
'F18_colored_burst':'成簇强相关噪声与漂移：数据质量拒绝自动放行',
}
COMMANDS=[(.6,80.),(2.5,-60.),(4.4,0.),(5.8,90.),(7.4,20.),(8.6,0.)]

def clip(x,lo,hi):return max(lo,min(hi,x))
def command_at(x):return next((v for t,v in reversed(COMMANDS) if x>=t),0.)
def sample(t,y,x):
    i=bisect_right(t,x)
    if i==0:return y[0]
    if i>=len(t):return y[-1]
    return y[i-1]+(y[i]-y[i-1])*(x-t[i-1])/(t[i]-t[i-1])
def timeline(ident,fs,rng):
    if ident=='F10_multirate_skew':
        t=[i/2000 for i in range(12001)]
        t += [6+i/8000 for i in range(1,10401)]
        t += [7.3+i/2000 for i in range(1,5401)]
        return t,[(2000,12001),(8000,22401),(2000,len(t))]
    t=[i/fs for i in range(round(10*fs)+1)]
    if ident=='F09_clock_jitter':
        t=[x if i in (0,len(t)-1) else round(x+rng.uniform(-.08,.08)/fs,6) for i,x in enumerate(t)]
    return t,[(fs,len(t))]

def generate_case(folder,ident,seed=20261011,fs=2000):
    if ident not in CATALOG:raise ValueError('未知 F 工况')
    if fs<1600:raise ValueError('该场景最高合成频率650Hz，采样率至少1600Hz；不生成欠采样的健康真值')
    rng=random.Random(seed);t,rates=timeline(ident,fs,rng);n=len(t)
    nominal=ident=='F01_coupled_nominal';delay_fault=ident=='F02_dispatch_delay';ring=ident=='F03_low_damping'
    stale=ident in ('F06_stale_telemetry','F16_compound_faults');emi=ident in ('F08_common_mode_emi','F16_compound_faults')
    trip_fault=ident in ('F05_trip_1ms','F16_compound_faults');ack_fault=ident in ('F14_handshake_loss','F16_compound_faults')
    deep=ident=='F04_deep_sag';unlimited=ident=='F15_limiter_disabled'
    signals={k:[] for k in ('p','p1','p2','set','q','dc','vbat','ibat','soc','f','ua','ub','uc','ia','ib','ic','irms','ub_ref','temp','limit')}
    truth=[];p=[0.,0.];v=[0.,0.];u=[0.,0.];energy=.5*1.2*750**2;energy0=energy;pb=0.;vp=0.;soc=.65;theta=0.;ar=0.;voltar=0.
    energy_error=0.;p_error=0.;mincap=math.inf
    for i,x in enumerate(t):
        dt=(t[i]-t[i-1]) if i else 0.
        freq=50+.025*math.sin(2*math.pi*.3*x) +(-.55*math.sin(math.pi*(x-6)/1.5) if 6<x<7.5 else 0)
        theta+=2*math.pi*freq*dt
        sag=6.45<=x<6.78
        residual=([.35,.28,.48] if deep else [.62,.50,.78]) if sag else [1.,.99,1.01]
        # Post-sag damped voltage recovery. Each phase shares event time, not arbitrary noise.
        rec=.07*math.exp(-(x-6.78)/.08)*math.sin(2*math.pi*22*(x-6.78)) if 6.78<=x<7.3 else 0.
        rms=[230*(z+rec) for z in residual];vm=sum(rms)/3
        qref=45*(1-min(residual)) if sag else 3*math.sin(2*math.pi*.12*x)
        cap=3*vm*160/1000;pcap=math.sqrt(max(0,cap**2-qref**2));mincap=min(mincap,pcap)
        for j,share in enumerate((.6,.4)):
            latency=(.62 if delay_fault and j==1 and 2.5<=x<4.4 else .025+.012*j)
            desired=share*command_at(x-latency)
            if not unlimited:desired=clip(desired,-share*pcap,share*pcap)
            ramp=500*share
            u[j]+=clip(desired-u[j],-ramp*dt,ramp*dt)
            wn=34 if j==0 else 29;zeta=.20 if ring else (.86 if j==0 else .93)
            v[j]+=(wn**2*(u[j]-p[j])-2*zeta*wn*v[j])*dt
            p[j]+=v[j]*dt
            if not unlimited:
                limited=clip(p[j],-share*pcap,share*pcap)
                if limited!=p[j]:p[j]=limited;v[j]=0.
        pac=sum(p);loss=1.2+.016*abs(pac)
        # Controlled bidirectional DC source feeding capacitor, energy balance in SI.
        vdc=math.sqrt(2*energy/1.2)
        pb_target=(pac+loss)*1000+(energy0-energy)/.075
        pb+=(pb_target-pb)*min(1.,dt/.025)
        old=energy;energy+=(pb-(pac+loss)*1000)*dt
        energy_error=max(energy_error,abs((energy-old)-(pb-(pac+loss)*1000)*dt))
        vdc=math.sqrt(2*energy/1.2)
        ibat=pb/(780-.07*(pb/780)-vp)
        vp+=(.015*ibat-vp)*min(1.,dt/.5);soc-=ibat*dt/(280*3600)
        vbat=780-.07*ibat-vp
        ar=.96*ar+rng.gauss(0,.025);voltar=.985*voltar+rng.gauss(0,.015)
        common=ar+.06*math.sin(2*math.pi*100*x)+.035*math.sin(2*math.pi*650*x)
        burst=0.
        if ident=='F18_colored_burst' and 1.65<x<1.99:burst=4.5*math.sin(2*math.pi*17*x)+2*math.sin(2*math.pi*3.2*x)
        impulse=32. if emi and 1.510<=x<1.511 else (-25. if emi and 3.510<=x<3.511 else 0.)
        cm=common+burst+impulse
        pm1=p[0]+.6*cm+rng.gauss(0,.025);pm2=p[1]+.4*cm+rng.gauss(0,.025);pm=pac+cm+rng.gauss(0,.02)
        if ident=='F13_sensor_gain_drift' and x>=7.5:pm*=1+.02*min(1,(x-7.5)/.3)
        vals={'p':pm,'p1':pm1,'p2':pm2,'set':command_at(x),'q':qref,'dc':vdc+.5*math.sin(2*theta)+voltar+(35 if impulse else 0),
              'vbat':vbat+voltar,'ibat':ibat+.03*math.sin(theta),'soc':soc*100,'f':freq,
              'temp':27+.018*abs(pac)+.06*math.sin(x),'limit':pcap}
        phi=theta+(math.radians(12) if x>=6.45 else 0)
        # Fundamental P/Q current and 5th/7th/11th harmonics; observed 650Hz ripple is explicitly synthetic.
        current_rms=math.hypot(pac,qref)*1000/(3*vm)
        vals['irms']=current_rms+rng.uniform(-.02,.02)
        for j,phase in enumerate(('a','b','c')):
            ang=phi-j*2*math.pi/3
            voltage=math.sqrt(2)*rms[j]*(math.sin(ang)+.018*math.sin(5*ang+.3)+.012*math.sin(7*ang)+.006*math.sin(11*ang))
            voltage+=voltar+.08*math.sin(2*math.pi*650*x)
            signed_phase=math.atan2(qref,pac) if abs(pac)+abs(qref)>1e-12 else 0
            dc_offset=9*math.exp(-(x-6.45)/.045) if sag else 0.
            current=math.sqrt(2)*current_rms*(math.sin(ang-signed_phase)+.025*math.sin(5*ang)+.015*math.sin(7*ang))+dc_offset*(1 if j==0 else -.5)
            vals['u'+phase]=voltage+rng.gauss(0,.035);vals['i'+phase]=current+rng.gauss(0,.02)
            if phase=='b':vals['ub_ref']=voltage+rng.gauss(0,.035)
        if ident=='F07_ct_vt_clipping' and 1.0<=x<1.8:vals['ub']=clip(vals['ub'],-215,215)
        for k in signals:signals[k].append(round(vals[k]/(.01 if k not in ('soc','f') else .0001))*(.01 if k not in ('soc','f') else .0001))
        if i%max(1,int(fs/100))==0:truth.append([x,p[0],p[1],pac,vdc,vbat,soc,current_rms,pcap])
    if stale:
        frozen=signals['p'][bisect_right(t,2.55)-1]
        for i,x in enumerate(t):
            if 2.55<=x<2.79:signals['p'][i]=frozen
    aliases=[('p','并网总表有功','kW'),('p1','PCS1有功','kW'),('p2','PCS2有功','kW'),('set','EMS有功设定','kW'),('q','并网无功','kvar'),
        ('dc','直流母线电压','V'),('vbat','电池簇端电压','V'),('ibat','电池簇电流','A'),('soc','电池簇SOC','%'),('f','PLL频率','Hz'),
        ('ua','并网Ua','V'),('ub','并网Ub','V'),('uc','并网Uc','V'),('ia','并网Ia','A'),('ib','并网Ib','A'),('ic','并网Ic','A'),
        ('irms','并网电流包络','A'),('ub_ref','B相独立测量','V'),('temp','PCS散热器温度','degC'),('limit','有功限幅边界','kW')]
    aa=[analog(k,name,signals[k],unit,a=.01) for k,name,unit in aliases]
    if ident=='F10_multirate_skew':
        for j,ch in enumerate(aa):
            if ch['alias'] in ('p1','p2','ub_ref'):
                skew=.00075 if ch['alias']!='p2' else .00125;raw=ch['data'];ch['data']=[sample(t,raw,x+skew) for x in t];ch['skew_us']=skew*1e6
        aa[1].update(a=-.007,b=3.);aa[2].update(a=.006,b=-2.)
    def pulse(x,a,b):return a-1e-10<=x<b-1e-10
    ack_windows=[]
    for j,(e,_) in enumerate(COMMANDS):
        if ack_fault and j==1:continue
        ack_windows += [(e+.044,e+.0445),(e+.048,e+.0485),(e+.052,e+.088)]
    dd=[digital('cmd','EMS命令脉冲',[int(any(pulse(x,e,e+.018) for e,_ in COMMANDS)) for x in t]),
        digital('ack','PCS执行确认',[int(any(pulse(x,a,b) for a,b in ack_windows)) for x in t]),
        digital('pre','预充完成',[int(x>=.36) for x in t]),
        digital('closed','主接触器反馈',[int(x>=.405 or pulse(x,.397,.398) or pulse(x,.400,.401)) for x in t]),
        digital('trip','保护跳闸原始位',[int(trip_fault and pulse(x,6.56,6.561)) for x in t]),
        digital('uv','电网低压检测',[int(pulse(x,6.45,6.78)) for x in t]),
        digital('limit_active','电流限幅状态',[int(pulse(x,6.45,6.78) and not unlimited) for x in t]),
        digital('heartbeat','站控心跳',[int(x/.025)%2 for x in t]),
        digital('bms_ready','BMS允许',[int(x>=.3) for x in t]),
        digital('reset','复归命令',[int(pulse(x,9.1,9.125)) for x in t])]
    target_a,target_d=(128,384) if ident=='F17_512_channel_rack' else (24,104)
    for j in range(len(aa),target_a):
        data=[3.28+.005*math.sin(.5*x+j)+.001*signals['ibat'][i]+.003*math.sin(2*math.pi*(11+j%40)*x) for i,x in enumerate(t)]
        aa.append(analog('cell'+str(j),f'电池簇{j//16:02d}_单体{j%16:02d}',data,'V',a=.0001))
    for j in range(len(dd),target_d):
        # Mix latched states, narrow handshakes, and densely switching irrelevant channels.
        divisor=3+j%47
        values=[int(i//divisor)%2 if j%4==0 else int(pulse(x,(j%9)+.12,(j%9)+.15)) if j%4==1 else int(x>(j%9)+.2) if j%4==2 else 0 for i,x in enumerate(t)]
        dd.append(digital('aux'+str(j),f'机柜{j//32:02d}_遥信{j%32:02d}',values))
    if ident=='F12_selected_dropout':
        aa[2]['data'][bisect_right(t,6.62)-1]=None
    if ident=='F11_acquisition_gap':
        ids=[i for i,x in enumerate(t) if not 6.60<=x<6.62]
        t=[t[i] for i in ids]
        for ch in aa+dd:ch['data']=[ch['data'][i] for i in ids]
        rates=[(fs,len(t))]
    c=base(CATALOG[ident]);c['description']='分层约束合成；不是现场录波或经过校准的EMT。先定义工况真值，再独立执行断言。';mapping(c,aa,dd);expected={}
    robust={'hampel_window_s':.007,'hampel_sigma':4.5,'hampel_floor':.35,'max_spike_s':.0015,'smooth_window_s':.002,
        'crossing_hold_s':.008,'crossing_hysteresis_fraction':.005,'settle_occupancy':.99,'max_excursion_s':.004,
        'max_replaced_fraction':.015,'max_noise_sigma':.7,'max_baseline_drift':.5,'max_steady_drift':.5,'raw_min':-150,'raw_max':150}
    for j,(e,target) in enumerate(COMMANDS[:3],1):
        out='PASS'
        if ring:out='FAIL'
        if delay_fault and j==2:out='FAIL'
        # A short telemetry freeze can STILL satisfy the configured step metrics.
        # It is detected by the independent sum-consistency assertion, not relabelled.
        if emi and j in (1,2):out='INCONCLUSIVE'
        if ident=='F18_colored_burst' and j==1:out='INCONCLUSIVE'
        add(c,expected,f'P{j}','analog_step','p',[0,1.4],outcome=out,
            trigger={'type':'digital_edge','channel':'cmd','edge':'rising','occurrence':j,'search':[0,5.7]},
            target=target,baseline_window=[-.18,-.04],steady_last_s=.25,normalization='rated',rated=100,
            settle_band_pct=2,settle_hold_s=.12,robust=copy.deepcopy(robust),
            limits={'overshoot_pct_max':4,'response_s_max':.55,'rise_s_max':.50,'settling_s_max':.85,'steady_error_pct_max':.5,'steady_ripple_pp_max':1.5})
    noise={'rise_s':.002,'fall_s':.002,'max_rejected_edges':8,'max_chatter_s':.01,'timing_guard':True}
    for j,(e,_) in enumerate(COMMANDS,1):
        add(c,expected,f'ACK{j}','digital_edge','ack',[0,.18],outcome='FAIL' if ack_fault and j==2 else 'PASS',
            trigger={'type':'time','at':e},edge='rising',count=1,latency_min_s=.03,latency_max_s=.09,noise=noise)
    add(c,expected,'CLOSE','digital_edge','closed',[0,.12],trigger={'type':'digital_edge','channel':'pre','edge':'rising'},edge='rising',count=1,latency_min_s=.02,latency_max_s=.09,noise=noise)
    add(c,expected,'NO_TRIP','digital_hold','trip',[.2,9.8],value=0,outcome='FAIL' if trip_fault else ('INCONCLUSIVE' if ident=='F11_acquisition_gap' else 'PASS'))
    for phase in ('a','b','c'):
        add(c,expected,'U'+phase.upper(),'analog_rms','u'+phase,[1.08,1.48],min=218,max=245,outcome='FAIL' if phase=='b' and ident=='F07_ct_vt_clipping' else 'PASS')
    add(c,expected,'DC','analog_range','dc',[.3,9.5],min=690,max=810,outcome='INCONCLUSIVE' if ident=='F11_acquisition_gap' else 'PASS')
    add(c,expected,'I_LIMIT','analog_range','irms',[6.4,7.15],min=0,max=161,outcome='FAIL' if unlimited else ('INCONCLUSIVE' if ident=='F11_acquisition_gap' else 'PASS'))
    add(c,expected,'FREQ','analog_range','f',[5.5,8],min=49,max=51,outcome='INCONCLUSIVE' if ident=='F11_acquisition_gap' else 'PASS')
    for id,window in [('SUM_EARLY',[.4,5.7]),('SUM_SAG',[6.3,7.2]),('SUM_LATE',[7.5,9.7])]:
        out='PASS'
        if stale and id=='SUM_EARLY':out='FAIL'
        if ident=='F13_sensor_gain_drift' and id=='SUM_LATE':out='FAIL'
        if ident in ('F11_acquisition_gap','F12_selected_dropout') and id=='SUM_SAG':out='INCONCLUSIVE'
        add(c,expected,id,'analog_relation','p',window,outcome=out,references=[{'channel':'p1','gain':1},{'channel':'p2','gain':1}],tolerance=.4,max_violation_s=.002,hard_max_error=1.0)
    add(c,expected,'VT_REDUNDANT','analog_relation','ub',[1.05,1.50],references=[{'channel':'ub_ref','gain':1}],tolerance=.4,max_violation_s=.002,hard_max_error=1.0,
        outcome='FAIL' if ident=='F07_ct_vt_clipping' else ('INCONCLUSIVE' if ident=='F10_multirate_skew' else 'PASS'),alignment_error_bound=6.0 if ident=='F10_multirate_skew' else 0)
    # Truth is chosen from injection categories, independent of the engine.
    overall=next((s for s in ('ERROR','FAIL','INCONCLUSIVE') if s in expected.values()),'PASS')
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    cfg=write_record(folder,ident,t,aa,dd,fmt='FLOAT32',revision=2013,rates=rates,trigger_s=.6)
    case=folder/(ident+'.json');case.write_text(json.dumps(c,ensure_ascii=False,indent=2),encoding='utf-8')
    truthdoc={'id':ident,'title':CATALOG[ident],'seed':seed,'samples':len(t),'channels':{'analog':len(aa),'digital':len(dd)},
        'duration_s':t[-1]-t[0],'nominal_sample_rate':fs,'rates':rates,'expected':overall,'expected_tests':expected,
        'model':'独立双PCS二阶闭环 + 参考斜率限制 + P/Q电流容量 + DC电容能量 + 电池RC + 三相谐波 + 传感器共模/独立噪声 + 量化',
        'not_claimed':'非现场捕获；非开关器件EMT；未建模完整PLL电流环/保护控制逻辑；谐波和电流直流偏置为受约束外加扰动；非GB限值',
        'plant_parameters':{'C_dc_F':1.2,'vdc0_V':750,'battery_Voc_V':780,'R0_ohm':.07,'R1_ohm':.015,'battery_capacity_Ah':280,'current_limit_A':160,'control_wn_rad_s':[34,29]},
        'commands':COMMANDS,'grid_event':{'sag_s':[6.45,6.78],'residual_per_phase':[.35,.28,.48] if deep else [.62,.50,.78],'phase_jump_degrees':12},
        'injections':{'delay':delay_fault,'low_damping':ring,'telemetry_freeze_s':[2.55,2.79] if stale else None,'trip_s':[6.56,6.561] if trip_fault else None,'emi_spans_s':[[1.510,1.511],[3.510,3.511]] if emi else [],'missing_ack':2 if ack_fault else None},
        'independent_invariants':{'maximum_discrete_dc_energy_balance_error_J':energy_error,'minimum_P_capacity_kW':mincap},
        'latent_preview_columns':['time_s','p1_kW','p2_kW','p_total_kW','vdc_V','vbat_V','soc_fraction','i_envelope_A','P_cap_kW'],'latent_preview':truth,
        'cfg_sha256':hashlib.sha256(cfg.read_bytes()).hexdigest(),'dat_sha256':hashlib.sha256(cfg.with_suffix('.dat').read_bytes()).hexdigest()}
    (folder/(ident+'.truth.json')).write_text(json.dumps(truthdoc,ensure_ascii=False,indent=2),encoding='utf-8')
    return cfg,case,truthdoc

def generate_field(folder,seed=20261011):
    folder=Path(folder);entries=[]
    for n,ident in enumerate(CATALOG):
        sub=folder/('A_控制响应' if n<6 else 'B_采集测量' if n<12 else 'C_复合工况')
        cfg,case,truth=generate_case(sub,ident,seed=seed)
        entries.append({'cfg':str(cfg.relative_to(folder)),'case':str(case.relative_to(folder)),'expected':truth['expected'],'expected_tests':truth['expected_tests']})
    manifest={'family':'field-inspired reduced order, NOT field captures','cases':entries}
    (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8');return manifest
