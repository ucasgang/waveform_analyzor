"""Reproducible noise-analysis timing; optional developer benchmark, no third-party modules."""
from pathlib import Path
import json,platform,sys,time,statistics
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from esswave.comtrade import load
from esswave.engine import run
from esswave.noise_view import processed_record
from esswave import __version__
rows=[]
for key in ('N01_white_noise','N23_multirate_noise','N32_many_channels'):
 cfg=ROOT/'examples/noise'/f'{key}.cfg';case=json.loads(cfg.with_suffix('.json').read_text())
 parses=[];analyses=[];views=[]
 for _ in range(5):
  t=time.perf_counter();rec=load(cfg);parses.append(time.perf_counter()-t)
  t=time.perf_counter();r=run(rec,case);analyses.append(time.perf_counter()-t)
  t=time.perf_counter();processed_record(rec,case,r['results'][0]);views.append(time.perf_counter()-t)
 rows.append({'case':key,'samples':len(rec.time),'analog_channels':rec.meta['analog_count'],
 'digital_channels':rec.meta['digital_count'],'assertions':len(r['results']),'status':r['status'],
 'parse_s':parses,'analysis_s':analyses,'full_processed_view_s':views,
 'median_parse_s':statistics.median(parses),'median_analysis_s':statistics.median(analyses),
 'median_full_processed_view_s':statistics.median(views)})
result={'version':__version__,'platform':platform.platform(),'python':sys.version,'repeats':5,
 'note':'Same-process five repeats; OS filesystem cache may be warm. Parsing covers all channels. Analysis and processed view cover the selected assertion, not all channels. Not a hardware guarantee.', 'rows':rows}
p=ROOT/'reports/benchmark_noise.json';p.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2))
