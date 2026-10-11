"""Exercise the BUILT zipapp via real HTTP: all 18 full model cases in one batch."""
from pathlib import Path
import hashlib,io,json,os,socket,struct,subprocess,sys,tempfile,time,zipfile
from urllib.request import Request,urlopen
from urllib.error import HTTPError
ROOT=Path(__file__).resolve().parents[1]
def main():
    out=ROOT/'reports';out.mkdir(exist_ok=True);checks=[]
    def check(name,ok,**extra):
        checks.append({'name':name,'passed':bool(ok),**extra});print(name,bool(ok),flush=True)
        if not ok:raise AssertionError(name)
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    log=(out/'zipapp_http_v13.log').open('w');proc=subprocess.Popen([sys.executable,str(ROOT/'ESS_WaveTest.pyz'),'gui','--port',str(port),'--no-browser'],cwd=ROOT,stdout=log,stderr=log)
    base=f'http://127.0.0.1:{port}';token=''
    def req(path,data=None,auth=True):
        raw=data if isinstance(data,bytes) else (json.dumps(data,ensure_ascii=False).encode() if data is not None else None)
        try:
            with urlopen(Request(base+path,data=raw,headers={'X-ESS-Token':token if auth else ''}),timeout=120) as r:return r.status,r.read()
        except HTTPError as e:return e.code,e.read()
    started=time.perf_counter();summary={}
    try:
        for _ in range(100):
            try:status,index=req('/');break
            except OSError:time.sleep(.1)
        else:raise AssertionError('zipapp failed startup')
        import re
        token=re.search(r'window.ESS_TOKEN\s*=\s*["\']([^"\']+)',index.decode()).group(1)
        version=subprocess.check_output([sys.executable,str(ROOT/'ESS_WaveTest.pyz'),'--version'],text=True).strip()
        check('built_zipapp_version',version=='1.3.0')
        check('batch_folder_input_and_explicit_zoom_assets',b'webkitdirectory' in index and b'data-action="in"' in req('/timeline.js')[1])
        check('batch_token_gate',req('/api/batch/current',auth=False)[0]==403)
        check('bad_request_keeps_service_alive',req('/api/batch/create',[])[0]==400 and req('/api/template')[0]==200)
        entries=json.loads((ROOT/'examples/field/manifest.json').read_text())['cases'];listing=[];paths={};expected={}
        for e in entries:
            p=ROOT/'examples/field'/e['cfg'];expected[e['cfg']]=e
            for q in (p,p.with_suffix('.dat'),ROOT/'examples/field'/e['case']):
                rel=q.relative_to(ROOT/'examples/field').as_posix();listing.append({'path':rel,'size':q.stat().st_size});paths[rel]=q
        s,b=req('/api/batch/create',{'files':listing,'mode':'sibling','recursive':True});job=json.loads(b);ident=job['id']
        check('18_nested_recordings_pair_without_cross_directory_mix',s==200 and job['total']==18 and job['counts']['ERROR']==0)
        upload_started=time.perf_counter()
        for row in job['rows']:
            a,b,c=[paths[row[k]].read_bytes() for k in ('cfg','dat','case')]
            status,_=req(f'/api/batch/upload/{ident}/{row["id"]}',struct.pack('<II',len(a),len(c))+a+b+c)
            if status!=200:raise AssertionError('upload '+row['cfg'])
        check('all_source_files_uploaded_and_hashed',json.loads(req('/api/batch/status/'+ident)[1])['counts']['READY']==18)
        upload_s=time.perf_counter()-upload_started;analysis_started=time.perf_counter();check('start_background_sequential_queue',req('/api/batch/start/'+ident,{})[0]==200)
        states=[]
        for _ in range(1000):
            snap=json.loads(req('/api/batch/status/'+ident)[1]);states.append(snap['state'])
            if snap['state']=='COMPLETE':break
            time.sleep(.15)
        else:raise AssertionError('batch timeout')
        analysis_s=time.perf_counter()-analysis_started
        check('all_18_finished_not_cancelled',snap['done']==18 and snap['counts']['CANCELLED']==0)
        check('actual_acceptance_not_inverted_by_expected_failures',snap['verdict']=='FAIL' and snap['counts']['FAIL']>0)
        check('per_record_overall_status_matches_injection_truth',all(r['status']==expected[r['cfg']]['expected'] for r in snap['rows']))
        status,archive=req('/api/batch/export/'+ident);check('actual_zip_download',status==200)
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            check('zip_crc_ok_and_no_copied_sources',z.testzip() is None and not any(n.lower().endswith(('.dat','.cfg')) for n in z.namelist()))
            actual=[]
            for row in snap['rows']:
                prefix='items/'+row['id']+'/out/';r=json.loads(z.read(prefix+'result.json'));exp=expected[row['cfg']]
                actual.append({t['id']:t['status'] for t in r['results']}==exp['expected_tests'])
                if b'wave-report-data' not in z.read(prefix+'report.html'):raise AssertionError('static report '+row['cfg'])
            check('378_business_assertions_and_all_interactive_reports',all(actual) and sum(len(e['expected_tests']) for e in entries)==378)
            exported=json.loads(z.read('summary.json'));check('summary_counts_match_status',exported['counts']==snap['counts'])
        row=snap['rows'][-1];s,b=req('/api/batch/open/'+ident+'/'+row['id'],{});opened=json.loads(b)
        check('open_saved_batch_result_without_rerun',s==200 and opened['result']['status']==row['status'])
        check('stale_trace_binding_rejected',req('/api/trace?key=A1&record_hash=old')[0]==400)
        s,b=req('/api/trace?key=A1&start=6.56&end=6.561&limit=8000');trace=json.loads(b)
        check('precise_zoom_reads_local_original_samples',s==200 and trace['mode']=='samples' and trace['source_samples']<10)
        (out/'18组复合工况_批处理报告.zip').write_bytes(archive)
        summary={'source_selected_bytes':sum(p['size'] for p in listing),'upload_s':upload_s,'parse_analyze_export_queue_s':analysis_s,'report_zip_bytes':len(archive),'report_zip_sha256':hashlib.sha256(archive).hexdigest(),'batch':snap}
    finally:
        proc.terminate()
        try:proc.wait(timeout=10)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()
        log.close()
        report={'version':'1.3.0','execution':'built zipapp, real loopback HTTP, no browser transport bridge','python':sys.version,'elapsed_s':time.perf_counter()-started,'total':len(checks),'passed':sum(c['passed'] for c in checks),'checks':checks,**summary}
        (out/'release_smoke_v13.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if checks and all(c['passed'] for c in checks) else 1
if __name__=='__main__':raise SystemExit(main())
