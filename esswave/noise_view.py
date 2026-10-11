"""Reconstruct full-resolution processed evidence for display only.
The authoritative result is never modified by zooming or layer toggles.
"""
from array import array
from dataclasses import replace
from .comtrade import Record
from .engine import resolve,segment
from .robust import condition,digital_qualify


def processed_record(record,configuration,item):
    a=next(v for v in configuration['tests'] if v['id']==item['id'])
    e=item['evidence'];sig=resolve(record,a['channel'],configuration);start,end=e['window_absolute_s']
    if 'robust' in a:
        t,y=segment(record,sig,start,end,a.get('max_gap_s'));clean,audit=condition(t,y,a['robust'])
        if audit['processed_sha256']!=e['processing']['analysis']['processed_sha256']:
            raise ValueError('处理曲线与分析时哈希不一致；请重新运行，禁止显示过期证据')
        markers=e['processing']['analysis']['repairs'];rejected=[]
    elif 'noise' in a:
        clean,events,pending,rejected=digital_qualify(sig.time,sig.values,a['noise'],end)
        t=sig.time[:len(clean)];markers=[]
    else:raise ValueError('此断言没有已计算的抗噪分支')
    ch=replace(sig.channel,skew_us=0.,unit=sig.unit)
    view=Record(record.meta,[ch],array('d',t),{ch.key:array('d',clean)},[],[],record.hashes)
    return view,markers,rejected
