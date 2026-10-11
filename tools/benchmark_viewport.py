"""Measured local viewport costs; not browser FPS or a hardware guarantee."""
from pathlib import Path
import sys,time,json,platform,statistics,math
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from esswave.comtrade import load
from esswave.trace import trace_window
from esswave import __version__
start=time.perf_counter();record=load(ROOT/'examples/complex/C09_dense_512.cfg');parse=time.perf_counter()-start
out={'version':__version__,'generated_utc':datetime.now(timezone.utc).isoformat(),'python':sys.version,'platform':platform.platform(),
     'samples':len(record.time),'analog_channels':128,'digital_channels':384,'parse_s':parse,'benchmarks':[],
     'scope':'当前运行环境实测。仅解析和视窗数据生成，不含浏览器渲染、HTTP往返、网络或工控机性能保证。'}
for name,key,a,b in [('analog_overview','A128',0,2),('analog_2ms_window','A128',1.234,1.236),
                    ('digital_dense_overview','D300',0,2),('digital_3ms_window','D300',1.234,1.237)]:
    times=[]
    for _ in range(15):
        t=time.perf_counter();d=trace_window(record,key,a,b,1800);times.append((time.perf_counter()-t)*1000)
    out['benchmarks'].append({'name':name,'mode':d['mode'],'source_samples':d['source_samples'],'display_points':d['display_points'],
           'repetitions':len(times),'p50_ms':statistics.median(times),'p95_ms':sorted(times)[math.ceil(.95*len(times))-1]})
(ROOT/'reports/viewport_benchmark.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False,indent=2))
