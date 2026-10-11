"""Generate and verify model-injection truth, independent of pass/fail engine."""
from pathlib import Path
import sys,json,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from esswave.field_cases import CATALOG,generate_case
from esswave.comtrade import load
from esswave.engine import run
from esswave.report import export,atomic_text

def main():
    out=ROOT/'reports/field_v13';out.mkdir(parents=True,exist_ok=True);rows=[];start=time.perf_counter();entries=[]
    for n,ident in enumerate(CATALOG):
        sub=ROOT/'examples/field'/('A_控制响应' if n<6 else 'B_采集测量' if n<12 else 'C_复合工况')
        cfg,case,truth=generate_case(sub,ident)
        config=json.loads(case.read_text());record=load(cfg);result=run(record,config)
        actual={a['id']:a['status'] for a in result['results']};matched=actual==truth['expected_tests'] and result['status']==truth['expected']
        mismatch={k:{'expected':v,'actual':actual.get(k)} for k,v in truth['expected_tests'].items() if actual.get(k)!=v}
        print(ident,result['status'],matched,mismatch,flush=True)
        export(result,out/ident,record=record)
        rows.append({'id':ident,'status':result['status'],'expected':truth['expected'],'matched':matched,'tests':actual,'mismatches':mismatch,'samples':len(record.time),'channels':len(record.channels),'physics_invariants':truth['independent_invariants']})
        entries.append({'cfg':str(cfg.relative_to(ROOT/'examples/field')),'case':str(case.relative_to(ROOT/'examples/field')),'expected':truth['expected'],'expected_tests':truth['expected_tests']})
    atomic_text(ROOT/'examples/field/manifest.json',json.dumps({'cases':entries},ensure_ascii=False,indent=2))
    data={'cases':len(rows),'assertions':sum(len(r['tests']) for r in rows),'all_matched':all(r['matched'] for r in rows),'elapsed_s':time.perf_counter()-start,'results':rows}
    atomic_text(out/'summary.json',json.dumps(data,ensure_ascii=False,indent=2));return 0 if data['all_matched'] else 1
if __name__=='__main__':raise SystemExit(main())
