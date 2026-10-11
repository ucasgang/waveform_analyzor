"""Explicit cross-channel conservation/tracking assertion, never eval expressions.
All mapped channels must have identical engineering units. Piecewise-linear
alignment uses the UNION of all timestamps, preserving reference-only extremes.
"""
import math
from .engine import resolve,trigger_time,coverage,interpolate,criterion,compact_trace,ConfigError

def validate_relation(a,config):
    from .engine import fields,number
    terms=a.get('references')
    if not isinstance(terms,list) or not 1<=len(terms)<=16:raise ConfigError('references 需含 1..16 个线性项')
    aliases=set()
    for term in terms:
        fields(term,{'channel','gain'},'references 项')
        alias=term.get('channel')
        if not isinstance(alias,str) or alias not in config['channels'] or config['channels'][alias]['kind']!='analog':raise ConfigError('参考项必须引用已定义模拟通道')
        if alias==a['channel'] or alias in aliases:raise ConfigError('参考项不能重复或直接引用被测通道本身')
        aliases.add(alias)
        if number(term.get('gain',1),'gain')==0:raise ConfigError('gain 不能为零')
    for k in ('tolerance','max_violation_s'):
        number(a.get(k),'relation.'+k,0)
    if a['max_violation_s']>a['window'][1]-a['window'][0]:raise ConfigError('max_violation_s 不能大于完整窗口长度')
    number(a.get('offset',0),'relation.offset')
    number(a.get('alignment_error_bound',0),'alignment_error_bound',0)
    if 'hard_max_error' in a:number(a['hard_max_error'],'relation.hard_max_error',0)

def outside_duration(t,y,tol):
    """Exact time outside [-tol,tol] for the linear residual; no sample counting."""
    duration=0.;first=None
    for i in range(1,len(t)):
        a,b=y[i-1],y[i];cuts=[0.,1.]
        if a!=b:
            for threshold in (-tol,tol):
                f=(threshold-a)/(b-a)
                if 0<f<1:cuts.append(f)
        cuts.sort()
        for x,z in zip(cuts,cuts[1:]):
            mid=a+(b-a)*(x+z)/2
            if abs(mid)>tol+1e-12:
                duration+=(z-x)*(t[i]-t[i-1])
                if first is None:first=t[i-1]+x*(t[i]-t[i-1])
    return duration,first

def analyze_relation(record,config,a,cache):
    sig=cache.get(a['channel'])
    if sig is None:sig=resolve(record,a['channel'],config);cache[a['channel']]=sig
    trigger,trig=trigger_time(record,config,a,cache);start,end=trigger+a['window'][0],trigger+a['window'][1]
    lo,hi=coverage(record,sig,start,end,a.get('max_gap_s'));sources=[];times={start,end}
    signals=[sig]
    for term in a['references']:
        ref=cache.get(term['channel'])
        if ref is None:ref=resolve(record,term['channel'],config);cache[term['channel']]=ref
        if ref.channel.key==sig.channel.key:raise ConfigError('参考映射到同一被测通道，无法形成独立一致性检查')
        if ref.unit!=sig.unit:raise ConfigError('一致性断言单位不同，必须显式换算到同一 output_unit')
        coverage(record,ref,start,end,a.get('max_gap_s'));sources.append((ref,term.get('gain',1)));signals.append(ref)
    if len({s.channel.key for s in signals})!=len(signals):raise ConfigError('多个参考别名映射到相同原始通道')
    maxdt=0.
    for s in signals:
        l,h=coverage(record,s,start,end,a.get('max_gap_s'))
        times.update(t for t in s.time[l:h] if start<t<end)
        maxdt=max(maxdt,max(s.time[i]-s.time[i-1] for i in range(l+1,h)))
    if len(times)>2000000:raise ConfigError('一致性时间网格超过 200 万点，请缩短用例窗口')
    t=sorted(times);measured=[interpolate(sig.time,sig.values,x) for x in t]
    ref=[a.get('offset',0)+math.fsum(gain*interpolate(s.time,s.values,x) for s,gain in sources) for x in t]
    residual=[v-r for v,r in zip(measured,ref)]
    if any(not math.isfinite(v) for v in residual):raise ConfigError('跨通道线性组合溢出')
    peak=max(abs(v) for v in residual);duration,first=outside_duration(t,residual,a['tolerance'])
    bound=a.get('alignment_error_bound',0)
    certain,_=outside_duration(t,residual,a['tolerance']+bound)
    possible=(end-start) if bound>a['tolerance'] else outside_duration(t,residual,a['tolerance']-bound)[0]
    def bounded_check(name,value,lower,upper,maximum):
        c=criterion(name,value,None,maximum)
        if lower>maximum+1e-9:c.update(status='FAIL',reason='计入显式插值误差预算后仍超限')
        elif upper<=maximum+1e-9:c.update(status='PASS',reason='计入显式插值误差预算后满足限值')
        else:c.update(status='INCONCLUSIVE',reason='插值/同步误差预算覆盖判定边界；需要更高采样率或经审核的误差预算')
        c.update(lower_bound=lower,upper_bound=upper);return c
    checks=[bounded_check('relation_violation_s',duration,certain,possible,a['max_violation_s'])]
    if 'hard_max_error' in a:checks.append(bounded_check('relation_max_error',peak,max(0,peak-bound),peak+bound,a['hard_max_error']))
    return {'relation_max_error':peak,'relation_violation_s':duration,'tolerance':a['tolerance'],'alignment_error_bound':bound,'violation_duration_bounds_s':[certain,possible]},checks,{
        'trigger':trig,'window_absolute_s':[start,end],'mapping':sig.mapping,'max_sample_interval_s':maxdt,
        'trace_kind':'analog','unit':sig.unit,'raw_trace':compact_trace(t,measured),
        'reference_trace':compact_trace(t,ref),'residual_trace':compact_trace(t,residual),'first_violation_s':first,
        'reference_mappings':[dict(s.mapping,gain=gain) for s,gain in sources],
        'alignment':'所有通道经 skew 与显式单位换算后的时间戳并集，分段线性插值；禁止跨缺点/间隙；不做自动延迟优化',
        'definitions':'被测值 - (offset + Σ gain × 参考值)；超容差时间按线性残差精确积分。硬残差峰值不去毛刺。'}
