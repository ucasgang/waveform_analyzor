"""Verify every declared assertion and independent numeric oracle."""
from pathlib import Path
import json,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from esswave.noise_cases import generate_noise
from esswave.comtrade import load
from esswave.engine import run,ConfigError
from esswave.report import export,atomic_text

def main():
    folder=ROOT/'examples/noise';manifest=generate_noise(folder);out=ROOT/'reports/noise_v12';rows=[];started=time.perf_counter()
    for case in manifest['cases']:
        c=json.loads((folder/case['case']).read_text(encoding='utf-8'));reasons={};oracles=[]
        try:
            result=run(load(folder/case['cfg']),c);actual={r['id']:r['status'] for r in result['results']};status=result['status']
            for r in result['results']:reasons[r['id']]=r['reason']
            for iid,expect in case['metric_oracles'].items():
                m=next(x for x in result['results'] if x['id']==iid)['metrics']
                for key,(lo,hi) in expect.items():oracles.append({'metric':iid+'.'+key,'actual':m.get(key),'expected':[lo,hi],'matched':m.get(key) is not None and lo<=m[key]<=hi})
            export(result,out/case['id'])
        except ConfigError as e:
            actual={'CONFIGURATION':'ERROR'};status='ERROR';reasons={'CONFIGURATION':str(e)}
            atomic_text(out/case['id']/'error.json',json.dumps(reasons,ensure_ascii=False,indent=2))
        matched=status==case['expected'] and actual==case['expected_assertions'] and all(v['matched'] for v in oracles)
        row={'id':case['id'],'name':case['name'],'status':status,'expected':case['expected'],'matched':matched,
             'assertions':actual,'expected_assertions':case['expected_assertions'],'oracles':oracles,'reasons':reasons}
        rows.append(row);print(case['id'],status,'OK' if matched else 'MISMATCH',flush=True)
        if not matched:print(json.dumps(row,ensure_ascii=False,indent=2),flush=True)
    summary={'version':'1.2.0','cases':len(rows),'matched':sum(r['matched'] for r in rows),'assertions':sum(len(x['assertions']) for x in rows),
             'elapsed_s':time.perf_counter()-started,'rows':rows}
    atomic_text(out/'summary.json',json.dumps(summary,ensure_ascii=False,indent=2));return 0 if all(x['matched'] for x in rows) else 1
if __name__=='__main__':raise SystemExit(main())
