"""Self-contained report viewers; bounded embedding with explicit resolution labels."""
from __future__ import annotations
from bisect import bisect_left,bisect_right
from importlib import resources
import json, math
from .engine import resolve
from .noise_view import processed_record

MAX_EMBED_POINTS=600000

def full_trace(record,key,start,end,factor=1.,offset=0.):
    c=next(c for c in record.channels if c.key==key);shift=c.skew_us*1e-6
    lo=max(0,bisect_right(record.time,start-shift)-1);hi=min(len(record.time),bisect_left(record.time,end-shift)+1)
    gaps=[(a+shift,b+shift) for a,b in record.gaps if a+shift<end and b+shift>start]
    trace=[];g=0;prev=None
    for i in range(lo,hi):
        x=record.time[i]+shift;v=record.values[key][i]*factor+offset
        if prev is not None:
            while g<len(gaps) and gaps[g][1]<=prev:g+=1
            if g<len(gaps) and gaps[g][0]<x and gaps[g][1]>prev:trace.append([(prev+x)/2,None])
        trace.append([x,v if math.isfinite(v) else None]);prev=x
    return trace,gaps

def script_json(value):
    # Script data must be safe even if a channel / assertion id contains </script>.
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026').replace('\u2028','\\u2028').replace('\u2029','\\u2029')

def enhance(document,result,record=None):
    from .report import svg_trace
    store={};budget=MAX_EMBED_POINTS
    for n,item in enumerate(result['results']):
        e=item['evidence'];raw=e.get('raw_trace')
        if not raw or not e.get('mapping') or not e.get('window_absolute_s'):continue
        m=e['mapping'];a,b=e['window_absolute_s'];processed=e.get('processed_trace');resolution='summary';gaps=[]
        if record is not None:
            shift=m.get('skew_us',0)*1e-6
            count=max(0,bisect_left(record.time,b-shift)+1-max(0,bisect_right(record.time,a-shift)-1))
            needed=count*(2 if processed else 1)
            if needed<=budget:
                raw,gaps=full_trace(record,m['key'],a,b,m['factor_including_ratio'],m['offset'])
                if processed:
                    view,_,_=processed_record(record,result['configuration'],item)
                    processed,_=full_trace(view,m['key'],a,b)
                budget-=len(raw)+(len(processed) if processed else 0);resolution='full'
        ident=str(n);store[ident]={'raw':raw,'processed':processed,'gaps':gaps,'resolution':resolution,
            'kind':e.get('trace_kind','analog'),'unit':m.get('output_unit',''),'bounds':[a,b],
            'target':e.get('target'),'band':e.get('band_absolute',0),'trigger':e.get('trigger',{}).get('at'),
            'repairs':e.get('processing',{}).get('analysis',{}).get('repairs',[])[:2000]}
        fallback=svg_trace(item)
        document=document.replace(fallback,f'<div class="report-wave chartbox" data-wave-id="{ident}" data-resolution="{resolution}">{fallback}</div>',1)
    if not store:return document
    static=resources.files('esswave').joinpath('static')
    css=static.joinpath('style.css').read_text(encoding='utf-8')
    # Keep only namespaced viewer styles, not the workbench's body/sidebar reset.
    idx=css.index('.timeline')
    css=css[idx:]+'''\n.report-wave{margin:18px 0}.report-wave svg{margin:0}.report-wave .timeline-tools button{font-size:12px}.report-wave [hidden]{display:none!important}@media print{.timeline-tools,.timeline-gesture,.timeline-nav,.timeline-nav-labels,.timeline-layers,.timeline-status{display:none!important}.timeline-frame{break-inside:avoid}}'''
    efunc="const E=v=>String(v??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));"
    js=efunc+'\n'+static.joinpath('plot.js').read_text()+'\n'+static.joinpath('timeline.js').read_text()+'\n'+static.joinpath('offline.js').read_text()
    document=document.replace('</head>',f'<style>{css}</style></head>')
    return document.replace('</body>',f'<script type="application/json" id="wave-report-data">{script_json(store)}</script><script>{js}</script></body>')
