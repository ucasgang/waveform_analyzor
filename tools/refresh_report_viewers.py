"""Refresh the viewer of retained historical reports without rewriting verdicts.
Full samples are embedded only when BOTH source file hashes match a shipped pair.
"""
from pathlib import Path
import hashlib,json,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from esswave.comtrade import load
from esswave.report import html_report,atomic_text
pairs={}
for cfg in (ROOT/'examples').rglob('*.cfg'):
    dat=cfg.with_suffix('.dat')
    if dat.exists():pairs[(hashlib.sha256(cfg.read_bytes()).hexdigest(),hashlib.sha256(dat.read_bytes()).hexdigest())]=cfg
rows=[]
for path in sorted((ROOT/'reports').rglob('report.html')):
    result=path.with_name('result.json')
    if not result.exists():continue
    r=json.loads(result.read_text());h=r['record']['hashes'];cfg=pairs.get((h['cfg_sha256'],h['dat_sha256']));record=load(cfg) if cfg else None
    html=html_report(r,record=record)
    html=html.replace('</body>','<p style="font:12px sans-serif;color:#687b90">交互查看器 v1.3.0；历史判定版本以结果正文为准。图中明确标注完整窗口采样或证据摘要；缩放不是重新验收。</p></body>')
    atomic_text(path,html);rows.append({'file':str(path.relative_to(ROOT)),'original_result_version':r.get('version'), 'source_pair_hash_matched':bool(cfg),'interactive':'wave-report-data' in html})
atomic_text(ROOT/'reports/viewer_refresh_v13.json',json.dumps({'reports':len(rows),'with_matching_sources':sum(r['source_pair_hash_matched'] for r in rows),'rows':rows},ensure_ascii=False,indent=2))
print(len(rows),'reports updated')
