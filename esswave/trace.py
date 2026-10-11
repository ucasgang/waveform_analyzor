"""Viewport-based waveform transport. Display sampling never enters the engine.

Analog: min/max buckets; missing buckets are gaps, not invented interpolation.
Digital: exact transition neighbours when possible; otherwise an explicitly
labelled state envelope. Zooming retrieves the original local samples again.
"""
from __future__ import annotations
from bisect import bisect_left, bisect_right
import math
from .engine import compact_trace


def trace_window(record,key,start=None,end=None,limit=1400,*,factor=1.,offset=0.,output_unit=None,bounds=None):
    c=next((c for c in record.channels if c.key==key),None)
    if c is None: raise ValueError('无此通道')
    if type(limit) is not int or not 32<=limit<=8000: raise ValueError('limit 必须为 32..8000 的整数')
    shift=c.skew_us*1e-6
    full=[record.time[0]+shift,record.time[-1]+shift] if bounds is None else list(bounds)
    if not all(math.isfinite(x) for x in full) or full[1]<=full[0]: raise ValueError('无效通道时间范围')
    start=full[0] if start is None else start;end=full[1] if end is None else end
    for value in (start,end):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ValueError('时间范围必须为有限数值')
    if start>=end or start<full[0]-1e-9 or end>full[1]+1e-9: raise ValueError('所选时间窗口超出有效范围或结束不大于开始')
    start=max(full[0],start);end=min(full[1],end)
    if start>=end: raise ValueError('所选时间窗口过小')
    if not all(math.isfinite(x) for x in (factor,offset)): raise ValueError('显示缩放溢出')
    lo=max(0,bisect_right(record.time,start-shift)-1)
    hi=min(len(record.time),bisect_left(record.time,end-shift)+1)
    t=[x+shift for x in record.time[lo:hi]]
    y=[v*factor+offset for v in record.values[key][lo:hi]]
    n=len(t)
    gaps=[(l+shift,r+shift) for l,r in record.gaps if l+shift<end and r+shift>start]
    budget=limit//2 if gaps else limit
    mode='samples';envelope=[]
    if c.kind=='digital' and n>budget:
        ids={0,n-1}
        for i in range(1,n):
            if y[i]!=y[i-1]:ids.update((i-1,i))
            if len(ids)>budget:break
        if len(ids)<=budget:
            trace=[[t[i],y[i]] for i in sorted(ids)];mode='edges'
        else:
            # Mixed bins show uncertainty regions, never a fake precise edge.
            bucket=math.ceil(n/max(1,limit//2))
            for j in range(0,n,bucket):
                stop=min(n,j+bucket)
                a=max(start,t[j]);b=min(end,t[stop] if stop<n else t[-1])
                if b>a:envelope.append([a,b,min(y[j:stop]),max(y[j:stop])])
            trace=[];mode='digital_envelope'
    else:
        trace=compact_trace(t,y,budget);mode='samples' if n<=budget else 'minmax'
    # Break any drawn segment whose support crosses an acquisition gap. Gap
    # annotations may be capped for the UI, but ALL gaps break the curve.
    if gaps and trace:
        broken=[];g=0
        for i,p in enumerate(trace):
            if i:
                prev=trace[i-1][0]
                while g<len(gaps) and gaps[g][1]<=prev:g+=1
                if g<len(gaps) and gaps[g][0]<p[0] and gaps[g][1]>prev:
                    broken.append([(prev+p[0])*.5,None])
            broken.append(p)
        trace=broken
    warnings=[]
    if mode=='minmax':warnings.append('当前视窗为保峰抽稀；放大后重新读取该区间原始采样')
    elif mode=='digital_envelope':warnings.append('变位过密：显示状态包络，不表示精确边沿；请继续放大')
    elif mode=='edges':warnings.append('已保留本视窗全部数字边沿及其相邻采样')
    else:warnings.append('当前视窗显示原始采样（含边界相邻点）')
    missing=sum(not math.isfinite(v) for v in y)
    if missing:warnings.append(f'有 {missing} 个缺失或无效样本，断线显示；缺失桶周围细节需放大查看')
    if gaps:warnings.append(f'有 {len(gaps)} 处采样间隙，灰色区域不连线')
    warnings.append('预览缩放不改变断言窗口或测试结果')
    return {'key':key,'name':c.name,'unit':output_unit if output_unit is not None else c.unit,'kind':c.kind,
            'trace':trace,'digital_bins':envelope,'gaps':gaps[:256],'gap_count':len(gaps),
            'start_s':start,'end_s':end,'full_start_s':full[0],'full_end_s':full[1],
            'source_samples':n,'display_points':len(trace),'missing_samples':missing,'mode':mode,
            'warning':'；'.join(warnings),'record_hash':record.hashes['cfg_sha256']+record.hashes['dat_sha256']}
