"""Re-run every composite assertion and its independent metric oracle."""
import json,platform,sys,time
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from esswave.comtrade import load
from esswave.engine import run
from esswave.report import export
from esswave import __version__

def main():
    manifest=json.loads((ROOT/'examples/complex/manifest.json').read_text(encoding='utf-8'));cases=[];start=time.perf_counter()
    for n,e in enumerate(manifest['cases'],1):
        tick=time.perf_counter();r=run(load(ROOT/'examples/complex'/e['cfg']),json.loads((ROOT/'examples/complex'/e['case']).read_text(encoding='utf-8')))
        actual={a['id']:a for a in r['results']};assertions=[];oracles=[]
        for ident,expected in e['expected_assertions'].items():
            a=actual[ident];assertions.append({'id':ident,'expected':expected,'actual':a['status'],'matched':a['status']==expected,'reason':a['reason'],'metrics':a['metrics']})
        for ident,ms in e['metric_oracles'].items():
            for metric,(lo,hi) in ms.items():
                v=actual[ident]['metrics'][metric];oracles.append({'id':ident,'metric':metric,'expected_interval':[lo,hi],'actual':v,'matched':lo<=v<=hi})
        matched=r['status']==e['expected'] and all(a['matched'] for a in assertions+oracles)
        folder=ROOT/'reports/complex_regression'/f'{n:03d}_{e["id"]}';export(r,folder)
        cases.append({'id':e['id'],'name':e['name'],'expected':e['expected'],'actual':r['status'],'matched':matched,
             'record':{k:e[k] for k in ('samples','analog_channels','digital_channels','format','seed')},
             'elapsed_s':round(time.perf_counter()-tick,4),'assertions':assertions,'metric_oracles':oracles,'counts':r['counts'],
             'report':'complex_regression/'+folder.name+'/report.html'})
        print(e['id'],r['status'],len(assertions),'assertions',matched)
    data={'version':__version__,'generated_utc':datetime.now(timezone.utc).isoformat(),'platform':platform.platform(),'python':sys.version,
          'case_count':len(cases),'matched_cases':sum(c['matched'] for c in cases),'assertion_count':sum(len(c['assertions']) for c in cases),
          'matched_assertions':sum(a['matched'] for c in cases for a in c['assertions']),'metric_oracle_count':sum(len(c['metric_oracles']) for c in cases),
          'elapsed_s':time.perf_counter()-start,'cases':cases,'notice':'预期包含故意失败与数据不足；匹配预期不是全部设备通过。阈值为合成工况自定义。'}
    (ROOT/'reports/complex_audit.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if all(c['matched'] for c in cases) else 1
if __name__=='__main__':raise SystemExit(main())
