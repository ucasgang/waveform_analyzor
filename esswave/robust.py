"""Auditable, opt-in noise handling. Standard-library implementation.

No denoising of hard raw limits, no automatic widening of acceptance bands,
no gap filling. Time-domain windows are closed at the assertion boundary.
References (algorithm concepts, not acceptance thresholds):
NIST MAD: https://www.itl.nist.gov/div898/software/dataplot/refman2/auxillar/mad.htm
Hampel: https://www.mathworks.com/help/signal/ref/hampel.html
"""
from __future__ import annotations
from array import array
from bisect import bisect_left, bisect_right
import hashlib
import math
from statistics import median
import struct

ROBUST_FIELDS = {
    'hampel_window_s','hampel_sigma','hampel_floor','max_spike_s',
    'smooth_window_s','crossing_hold_s','crossing_hysteresis_fraction',
    'settle_occupancy','max_excursion_s','max_replaced_fraction','max_noise_sigma',
    'max_baseline_drift','max_steady_drift','raw_min','raw_max',
    'clip_min','clip_max','max_clip_fraction','metric_guard_pct','ripple_guard',
}
NOISE_FIELDS = {'rise_s','fall_s','max_rejected_edges','max_chatter_s','merge_gap_s','timing_guard'}
MAX_NEIGHBORS = 2001
MAX_AUDIT = 2000
MAX_NEIGHBOR_VISITS = 20_000_000


def validate_robust(r):
    from .engine import fields,number,ConfigError
    fields(r,ROBUST_FIELDS,'robust')
    required={'raw_min','raw_max','max_noise_sigma','max_replaced_fraction',
              'max_baseline_drift','max_steady_drift'}
    if required-set(r):raise ConfigError('robust 缺少质量/原始硬限值字段: '+', '.join(sorted(required-set(r))))
    for key,value in r.items():number(value,'robust.'+key,None if key in ('raw_min','raw_max','clip_min','clip_max') else 0)
    if r['raw_min']>=r['raw_max']:raise ConfigError('robust.raw_min 必须小于 raw_max')
    if r.get('hampel_window_s',0)>0:
        if r.get('hampel_sigma',4.5)<=0 or r.get('hampel_floor',0)<=0 or r.get('max_spike_s',0)<=0:
            raise ConfigError('启用 Hampel 必须指定正数 hampel_floor、max_spike_s；hampel_sigma 必须为正')
        if r['max_spike_s']>=r['hampel_window_s']/2:
            raise ConfigError('max_spike_s 必须小于 Hampel 窗宽的一半，禁止把长扰动作为孤立毛刺')
    if not 0<r.get('settle_occupancy',1)<=1:raise ConfigError('settle_occupancy 要求 0 < 值 <= 1')
    if not 0<=r['max_replaced_fraction']<=.1:raise ConfigError('max_replaced_fraction 要求 0..0.1；大比例改写不能用于自动验收')
    if not 0<=r.get('crossing_hysteresis_fraction',0)<.1:raise ConfigError('crossing_hysteresis_fraction 要求 0..0.1（不含 0.1）')
    clip={'clip_min','clip_max','max_clip_fraction'}
    if clip&set(r):
        if not clip<=set(r):raise ConfigError('削顶监测需同时配置 clip_min/clip_max/max_clip_fraction')
        if r['clip_min']>=r['clip_max'] or not 0<=r['max_clip_fraction']<=1:raise ConfigError('削顶参数无效')


def validate_noise(r):
    from .engine import fields,number,integer,ConfigError
    fields(r,NOISE_FIELDS,'noise')
    for k in ('rise_s','fall_s','max_chatter_s'):
        if k not in r:raise ConfigError('noise 必须填写 '+k)
    for k in ('rise_s','fall_s','max_chatter_s','merge_gap_s'):
        if k in r:number(r[k],'noise.'+k,0)
    integer(r.get('max_rejected_edges',20),'noise.max_rejected_edges',0)
    if 'timing_guard' in r and not isinstance(r['timing_guard'],bool):raise ConfigError('noise.timing_guard 必须是布尔值')


def weights(t):
    """Voronoi-in-time weights, not a statistical confidence distribution."""
    if len(t)<2 or any(t[i]<=t[i-1] for i in range(1,len(t))):raise ValueError('时间轴必须严格递增且至少有两个点')
    return [(t[1]-t[0])/2]+[(t[i+1]-t[i-1])/2 for i in range(1,len(t)-1)]+[(t[-1]-t[-2])/2]


def weighted_quantile(t,y,q=.5):
    if len(t)!=len(y) or not 0<=q<=1:raise ValueError('分位数输入无效')
    wy=sorted(zip(y,weights(t)));goal=q*(t[-1]-t[0]);acc=0.
    for value,w in wy:
        acc+=w
        if acc+1e-15>=goal:return float(value)
    return float(wy[-1][0])


def level_stats(t,y):
    center=weighted_quantile(t,y)
    sigma=1.4826*weighted_quantile(t,[abs(v-center) for v in y])
    span=t[-1]-t[0];i=bisect_right(t,t[0]+span*.25);j=bisect_left(t,t[-1]-span*.25)
    if i<2 or len(t)-j<2:raise ValueError('漂移估计的首尾四分之一窗口均至少需要两个采样点')
    drift=abs(weighted_quantile(t[j:],y[j:])-weighted_quantile(t[:i],y[:i]))
    return {'median':center,'sigma_mad':sigma,'drift':drift,
            'p01':weighted_quantile(t,y,.01),'p99':weighted_quantile(t,y,.99)}


def condition(t,y,r):
    """Time-window Hampel + bounded-island repair + optional centered median.

    Entire neighborhoods must be observed within this segment. Boundaries stay
    raw; unbounded/long islands stay raw. Missing values are never imputed.
    """
    from .engine import InsufficientData
    if len(t)!=len(y) or len(t)<2 or any(not math.isfinite(v) for v in y):
        raise InsufficientData('预处理输入包含缺失值或不足两个点；禁止插补后判通过')
    n=len(t);clean=list(y);med=list(y);thresholds=[0.]*n;flags=[False]*n
    eligible=0;undersampled=0;visited=0
    half=r.get('hampel_window_s',0)/2
    if half>0:
        left=0;right=0
        for i in range(n):
            while left<i and t[left]<t[i]-half-1e-12:left+=1
            while right<n and t[right]<=t[i]+half+1e-12:right+=1
            if right-left>MAX_NEIGHBORS:raise InsufficientData(f'抗噪窗口超过 {MAX_NEIGHBORS} 个采样点；请缩短时间窗，不会静默抽稀判定')
            if t[i]-half<t[0]-1e-12 or t[i]+half>t[-1]+1e-12:continue
            if i-left<2 or right-i-1<2:undersampled+=1;continue
            visited+=right-left
            if visited>MAX_NEIGHBOR_VISITS:raise InsufficientData('抗噪窗口总计算量超过保护上限；请拆分较短测试用例')
            eligible+=1
            v=y[left:right];m=median(v);mad=median(abs(x-m) for x in v)
            th=max(r['hampel_floor'],r.get('hampel_sigma',4.5)*1.4826*mad)
            med[i]=m;thresholds[i]=th;flags[i]=abs(y[i]-m)>th
    repairs=[];islands=[];i=0;total_duration=0.
    while i<n:
        if not flags[i]:i+=1;continue
        j=i
        while j+1<n and flags[j+1]:j+=1
        # The two outer observations bound the physical duration conservatively.
        duration=t[min(n-1,j+1)]-t[max(0,i-1)]
        bounded=i>0 and j+1<n and duration<=r.get('max_spike_s',0)+1e-12
        same_level=bounded and abs(y[j+1]-y[i-1])<=2*max(thresholds[i:j+1])
        replaced=bounded and same_level
        islands.append({'start_s':t[i],'end_s':t[j],'support_s':[t[max(0,i-1)],t[min(n-1,j+1)]],
                        'duration_upper_s':duration,'samples':j-i+1,'replaced':replaced})
        if replaced:
            total_duration+=duration
            for k in range(i,j+1):
                clean[k]=med[k];repairs.append({'time_s':t[k],'raw':float(y[k]),'replacement':float(med[k])})
        i=j+1
    repaired=list(clean);shalf=r.get('smooth_window_s',0)/2
    if shalf>0:
        # Sorted moving time window. No filtering of extrema in the hard-raw path.
        from bisect import insort
        active=[];left=0;right=0
        for i in range(n):
            while right<n and t[right]<=t[i]+shalf+1e-12:insort(active,repaired[right]);right+=1
            while left<i and t[left]<t[i]-shalf-1e-12:
                active.pop(bisect_left(active,repaired[left]));left+=1
            if len(active)>MAX_NEIGHBORS:raise InsufficientData(f'平滑窗口超过 {MAX_NEIGHBORS} 个点；请缩短窗口')
            if t[i]-shalf>=t[0]-1e-12 and t[i]+shalf<=t[-1]+1e-12:
                nn=len(active);clean[i]=active[nn//2] if nn%2 else (active[nn//2-1]+active[nn//2])/2
    ww=weights(t);replaced_set={p['time_s'] for p in repairs}
    fraction=math.fsum(w for tx,w in zip(t,ww) if tx in replaced_set)/(t[-1]-t[0])
    h=hashlib.sha256()
    for tx,v in zip(t,clean):h.update(struct.pack('<dd',tx,v))
    smoothed=sum(a!=b for a,b in zip(clean,repaired));changed=sum(a!=b for a,b in zip(clean,y))
    audit={'median_smoothed_samples':smoothed,'total_changed_samples':changed,
           'max_smoothing_change':max(abs(a-b) for a,b in zip(clean,repaired)),
           'eligible_windows':eligible,'undersampled_windows':undersampled,'candidate_samples':sum(flags),'replaced_samples':len(repairs),'replaced_fraction':fraction,
           'replaced_sample_fraction':len(repairs)/n,'island_duration_upper_sum_s':total_duration,
           'repairs':repairs[:MAX_AUDIT],'islands':islands[:MAX_AUDIT],
           'audit_truncated':len(repairs)>MAX_AUDIT or len(islands)>MAX_AUDIT,'processed_sha256':h.hexdigest(),
           'window_s':[t[0],t[-1]],'mode':'offline_centered_time_windows',
           'timing_support_radius_s':half+shalf,
           'note':'候选异常不是已证明的采集噪声。未覆盖完整窗口、长扰动与缺点不自动修复。'}
    return clean,audit


def qualified_crossing(t,y,threshold,hold=0.,hysteresis=0.):
    """First interpolated rising crossing, qualified by persistent observations.
    Candidate is cancelled below threshold-hysteresis. Return onset and evidence.
    No sample later than t[-1] can affect this decision.
    """
    if y[0]>=threshold:return None,{'pre_responded':True,'rejected':0,'pending':False}
    onset=None;support=None;rejected=0
    for i in range(1,len(t)):
        if onset is not None and y[i]<threshold-hysteresis:
            # Did qualification complete before this downward crossing?
            boundary=t[i-1]+(t[i]-t[i-1])*(y[i-1]-(threshold-hysteresis))/(y[i-1]-y[i])
            if onset+hold<=boundary+1e-12:
                return onset,{'onset_s':onset,'confirmed_s':onset+hold,'confirmation_sample_s':t[i],
                              'support_s':support,'rejected':rejected,'pending':False}
            onset=None;support=None;rejected+=1
        if onset is None and y[i-1]<threshold<=y[i]:
            onset=t[i-1]+(t[i]-t[i-1])*(threshold-y[i-1])/(y[i]-y[i-1]);support=[t[i-1],t[i]]
        if onset is not None and t[i]+1e-12>=onset+hold:
            return onset,{'onset_s':onset,'confirmed_s':onset+hold,'confirmation_sample_s':t[i],
                          'support_s':support,'rejected':rejected,'pending':False}
    return None,{'rejected':rejected,'pending':onset is not None,'candidate_onset_s':onset}


def band_intervals(t,y,target,band):
    """Exact in/out durations of the piecewise-linear reconstructed samples."""
    parts=[]
    for i in range(1,len(t)):
        a,b=t[i-1],t[i];v,w=y[i-1],y[i];cuts=[a,b]
        if v!=w:
            for level in (target-band,target+band):
                frac=(level-v)/(w-v)
                if 0<frac<1:cuts.append(a+(b-a)*frac)
        cuts.sort()
        for x,z in zip(cuts,cuts[1:]):
            mid=v+(w-v)*((x+z)/2-a)/(b-a);inside=abs(mid-target)<=band+1e-12
            if parts and parts[-1][2]==inside and abs(parts[-1][1]-x)<1e-10:parts[-1][1]=z
            else:parts.append([x,z,inside])
    return parts


def settling(t,y,target,band,hold,occupancy,max_excursion):
    parts=band_intervals(t,y,target,band);n=len(parts);bad=[0.]*(n+1);longest=[0.]*(n+1)
    for i in range(n-1,-1,-1):
        dur=0 if parts[i][2] else parts[i][1]-parts[i][0]
        bad[i]=bad[i+1]+dur;longest[i]=max(longest[i+1],dur)
    at=None;stats={'tail_occupancy':None,'longest_excursion_s':None}
    for i,(a,b,inside) in enumerate(parts):
        span=t[-1]-a
        if inside and span+1e-12>=hold and span>0:
            fraction=1-bad[i]/span
            if fraction+1e-12>=occupancy and longest[i]<=max_excursion+1e-12:
                at=a;stats={'tail_occupancy':fraction,'longest_excursion_s':longest[i]};break
    stats.update({'full_window_occupancy':1-bad[0]/(t[-1]-t[0]),
                  'full_window_longest_excursion_s':longest[0]})
    return at,stats


def digital_qualify(t,y,r,until):
    """Asymmetric minimum-dwell qualification with an audit of suppressed runs."""
    stop=bisect_right(t,until+1e-12);t=t[:stop];y=y[:stop]
    if not len(t):raise ValueError('去抖窗口没有数据')
    n=len(t);state=int(y[0]);out=array('B',[state])*n;events=[];pending=[];rejected=[];write=0;i=1
    while i<n:
        j=i+1
        while j<n and y[j]==y[i]:j+=1
        level=int(y[i]);finish=t[j] if j<n else t[-1];dwell=r['rise_s'] if level else r['fall_s']
        if level!=state:
            if finish-t[i]+1e-12>=dwell:
                for k in range(write,i):out[k]=state
                events.append({'time_s':t[i],'from':state,'to':level,'confirmed_s':t[i]+dwell,
                               'support_s':[t[i-1],t[i]],'confirmation_sample_s':t[min(n-1,bisect_left(t,t[i]+dwell-1e-12))]})
                state=level;write=i
            elif j==n:pending.append(t[i])
            else:rejected.append({'start_s':t[i],'end_s':finish,'width_s':finish-t[i],'level':level,
                                  'raw_edges':2,'support_s':[t[i-1],t[j]]})
        i=j
    for k in range(write,n):out[k]=state
    return out,events,pending,rejected


def digital_audit(t,y,events,rejected,start,end,r):
    selected=[p for p in rejected if p['end_s']>=start-1e-12 and p['start_s']<=end+1e-12]
    merge=r.get('merge_gap_s',max(r['rise_s'],r['fall_s']));bursts=[]
    for p in selected:
        a=max(start,p['start_s']);b=min(end,p['end_s'])
        if bursts and a-bursts[-1][1]<=merge+1e-12:bursts[-1][1]=b
        else:bursts.append([a,b])
    lo=max(1,bisect_left(t,start-1e-12));hi=bisect_right(t,end+1e-12)
    raw=sum(y[i]!=y[i-1] for i in range(lo,hi));qualified=sum(start-1e-12<=e['time_s']<=end+1e-12 for e in events)
    return {'raw_edges':raw,'qualified_edges':qualified,'rejected_edges':sum(int(start-1e-12<=p['start_s']<=end+1e-12)+int(start-1e-12<=p['end_s']<=end+1e-12) for p in selected),
            'rejected_runs':selected[:MAX_AUDIT],'burst_windows_s':bursts[:MAX_AUDIT],
            'longest_chatter_s':max((b-a for a,b in bursts),default=0.),
            'audit_truncated':len(selected)>MAX_AUDIT or len(bursts)>MAX_AUDIT,'configuration':r,
            'note':'去抖只执行显式配置，不能证明被抑制的脉冲不是保护动作。保护通道请保留原始断言。'}


def trigger_crossings(t,y,start,end,threshold,direction,hysteresis,hold):
    """Schmitt-armed analog trigger. Effective rising level is threshold+h.

    Require an observed opposite level before arming. Each completed event is
    disarmed until a new opposite-level observation. End is a strict evidence
    cutoff (no following sample used to qualify an event).
    """
    lo=max(0,bisect_left(t,start)-1);hi=bisect_right(t,end+1e-12)
    effective=direction*threshold+hysteresis;reset=direction*threshold-hysteresis
    armed=False;candidate=None;support=None;events=[];rejected=0
    for i in range(lo,hi):
        v=direction*y[i]
        if v<reset:armed=True
        if i==lo:continue
        prev=direction*y[i-1]
        if candidate is not None and v<effective:
            off=t[i-1]+(t[i]-t[i-1])*(prev-effective)/(prev-v)
            if candidate+hold<=off+1e-12:
                events.append({'time_s':candidate,'confirmed_s':candidate+hold,'confirmation_sample_s':t[i],'support_s':support})
                armed=False
            else:rejected+=1
            candidate=None
            if v<reset:armed=True
        if armed and candidate is None and prev<effective<=v:
            hit=t[i-1]+(t[i]-t[i-1])*(effective-prev)/(v-prev)
            if start-1e-12<=hit<=end+1e-12:candidate=hit;support=[t[i-1],t[i]]
        if candidate is not None and t[i]+1e-12>=candidate+hold:
            events.append({'time_s':candidate,'confirmed_s':candidate+hold,'confirmation_sample_s':t[i],'support_s':support})
            armed=False;candidate=None
    return events,{'rejected_candidates':rejected,'pending':candidate is not None,
                   'effective_threshold':direction*effective,'hysteresis':hysteresis,'hold_s':hold}


def analyze_robust_step(record,config,a,cache):
    from .engine import (analyze_one,segment,resolve,interpolate,criterion,compact_trace,
                         ConfigError,InsufficientData,mean_time)
    plain=dict(a);r=plain.pop('robust')
    raw_metrics,raw_checks,evidence=analyze_one(record,config,plain,cache)
    sig=cache[a['channel']];t0=evidence['trigger']['at'];start,end=evidence['window_absolute_s'];gap=a.get('max_gap_s')
    t,y=segment(record,sig,start,end,gap)
    bt,by=segment(record,sig,t0+a['baseline_window'][0],t0+a['baseline_window'][1],gap)
    # Samples outside each sealed window cannot leak into the filter neighborhood.
    clean,audit=condition(t,y,r);bclean,baudit=condition(bt,by,r)
    baseline_stats=level_stats(bt,by);baseline=weighted_quantile(bt,bclean)
    target=raw_metrics['target'];delta=target-baseline
    if abs(delta)<=1e-12*max(1.,abs(target),abs(baseline)):raise InsufficientData('抗噪基线与目标无有效阶跃')
    norm=a.get('normalization','step');denom=abs(delta) if norm=='step' else (abs(target) if norm=='target' else a['rated'])
    if denom<=1e-12:raise ConfigError('百分比归一化分母为零')
    sign=1 if delta>0 else -1;progress=[(v-baseline)/delta for v in clean]
    hold=r.get('crossing_hold_s',0);hys=r.get('crossing_hysteresis_fraction',0)
    low,low_e=qualified_crossing(t,progress,a.get('low_fraction',.1),hold,hys)
    high,high_e=qualified_crossing(t,progress,a.get('high_fraction',.9),hold,hys)
    low=None if low is None else low-t0;high=None if high is None else high-t0
    band=denom*a.get('settle_band_pct',2)/100;settle_hold=a.get('settle_hold_s',.1)
    sat,sstats=settling(t,clean,target,band,settle_hold,r.get('settle_occupancy',1),r.get('max_excursion_s',0))
    ss_start=end-a.get('steady_last_s',.2)
    st,sy=segment(record,sig,ss_start,end,gap)
    # Steady statistics are evaluated on the same conditioned analysis trace.
    sc=[interpolate(t,clean,tx) for tx in st];steady_stats=level_stats(st,sy);steady=weighted_quantile(st,sc)
    peak_i=max(range(len(clean)),key=lambda i:sign*clean[i])
    metrics={'baseline':baseline,'target':target,'step_amplitude':delta,'normalization':norm,'denominator':denom,
             'overshoot_pct':max(0.,sign*(clean[peak_i]-target))/denom*100,
             'peak':clean[peak_i],'peak_time_s':t[peak_i]-t0,'latency_s':low,'response_s':high,
             'rise_s':None if low is None or high is None else high-low,
             'settling_s':None if sat is None else sat-t0,'steady_mean':mean_time(st,sc),'steady_median':steady,'steady_estimator':'time_weighted_median',
             'steady_error_pct':abs(steady-target)/denom*100,'steady_ripple_pp':max(sc)-min(sc),
             'settle_band_absolute':band,'baseline_sigma_mad':baseline_stats['sigma_mad'],
             'steady_sigma_mad':steady_stats['sigma_mad'],'baseline_drift':baseline_stats['drift'],
             'steady_drift':steady_stats['drift'],'replaced_fraction':max(audit['replaced_fraction'],baudit['replaced_fraction']),
             'replaced_samples':audit['replaced_samples']+baudit['replaced_samples'],
             'raw_peak':raw_metrics['peak'],'raw_overshoot_pct':raw_metrics['overshoot_pct'],
             'raw_steady_ripple_pp':raw_metrics['steady_ripple_pp'],**sstats}
    # This is an engineering review guard, NOT a calibrated confidence interval.
    guard=max(a.get('guard_band_s',0),evidence['max_sample_interval_s']+audit['timing_support_radius_s']+
              evidence['trigger'].get('uncertainty_s',0))
    checks=[]
    for lim,maximum in a['limits'].items():
        key=lim[:-4];value=metrics[key]
        metric_guard=(guard*(2 if key=='rise_s' else 1) if key.endswith('_s') else
                      r.get('metric_guard_pct',0) if key in ('overshoot_pct','steady_error_pct') else r.get('ripple_guard',0))
        c=criterion(key,value,None,maximum,metric_guard)
        if value is None:
            needed=(low+maximum+hold if low is not None else math.inf) if key=='rise_s' else maximum+(settle_hold if key=='settling_s' else hold)
            enough=end-t0>=needed-1e-12 and progress[0]<a.get('low_fraction',.1)
            c.update(status='FAIL' if enough else 'INCONCLUSIVE',reason='充分观察后仍未取得持续确认的响应/稳定状态' if enough else '观察不足或已提前响应，无法给出可靠时间')
        checks.append(c)
    quality=[]
    for key,value,maximum in [('repair_fraction',metrics['replaced_fraction'],r['max_replaced_fraction']),
                               ('baseline_noise_sigma',baseline_stats['sigma_mad'],r['max_noise_sigma']),
                               ('steady_noise_sigma',steady_stats['sigma_mad'],r['max_noise_sigma']),
                               ('baseline_drift',baseline_stats['drift'],r['max_baseline_drift']),
                               ('steady_drift',steady_stats['drift'],r['max_steady_drift'])]:
        c=criterion(key,value,None,maximum)
        if c['status']=='FAIL':c.update(status='INCONCLUSIVE',reason='超过预先配置的数据质量预算；禁止扩大误差带或自动提高降噪强度')
        quality.append(c)
    if 'clip_min' in r:
        ww=weights(t);eps=1e-8*max(1,abs(r['clip_min']),abs(r['clip_max']))
        frac=math.fsum(w for v,w in zip(y,ww) if v<=r['clip_min']+eps or v>=r['clip_max']-eps)/(end-start)
        metrics['clip_fraction']=frac;c=criterion('clip_fraction',frac,None,r['max_clip_fraction'])
        if c['status']=='FAIL':c.update(status='INCONCLUSIVE',reason='到达配置传感器量程边界的时间过多；削顶数据不能证明响应性能')
        quality.append(c)
    under=audit['undersampled_windows']+baudit['undersampled_windows']
    if under:
        quality.append({'metric':'preprocessing_resolution','value':under,'min':0,'max':0,'status':'INCONCLUSIVE',
                        'reason':'配置的时间窗内不足左右各两个采样；滤波尺度未被采样系统分辨，不能假定降噪已有效执行'})
    if audit['audit_truncated'] or baudit['audit_truncated']:
        quality.append({'metric':'complete_repair_audit','value':0,'min':1,'max':1,'status':'INCONCLUSIVE','reason':'修复记录超过审计上限；保留原文件并缩短用例窗口'})
    # Primary quality gates invalidate processed performance claims, not hard raw evidence.
    if any(c['status']!='PASS' for c in quality):
        for c in checks:c.update(status='INCONCLUSIVE',reason='数据质量门禁未通过；指标保留作诊断，不作为设备合格/不合格结论')
    # A filtered-away peak cannot be silently labelled a measurement artifact.
    changes=[c['metric'] for c in raw_checks if c['metric'] in ('overshoot_pct','steady_ripple_pp') and c['status']=='FAIL'
             and any(v['metric']==c['metric'] and v['status']=='PASS' for v in checks)]
    if changes:
        quality.append({'metric':'raw_peak_review','value':len(changes),'min':0,'max':0,'status':'INCONCLUSIVE',
                        'reason':'原始 '+', '.join(changes)+' 越限被处理曲线消除；仅凭波形不能确认是采集毛刺，必须复核'})
    rawchecks=[criterion('raw_minimum',min(y+by),r['raw_min'],None),criterion('raw_maximum',max(y+by),None,r['raw_max'])]
    for c in rawchecks:
        if c['status']=='FAIL':c['reason']='未经滤波的原始样本超过硬限值；降噪不能解除此越限'
    checks+=quality+rawchecks
    evidence.update({'raw_metrics':raw_metrics,'raw_checks':raw_checks,'processed_trace':compact_trace(t,clean),
                     'processing':{'configuration':r,'analysis':audit,'baseline':baudit,'quality_checks':quality,
                                   'raw_peak_conflicts':changes,'timing_review_guard_s':guard},
                     'baseline_statistics':baseline_stats,'steady_statistics':steady_stats,
                     'qualified_crossings':{'low':low_e,'high':high_e},'target':target,'band_absolute':band,
                     'definitions':'抗噪分支：离线居中时间窗；稳态使用时间加权中位数；阈值穿越必须持续确认；固定误差带+尾段占用率+最长连续出带；原始硬限值永不滤波。'})
    return metrics,checks,evidence
