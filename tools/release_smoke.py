from pathlib import Path
import subprocess,sys,time,urllib.request,json,re
root=Path(__file__).resolve().parents[1];checks=[]
import socket
with socket.socket() as sock:
 sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
process=subprocess.Popen([sys.executable,str(root/'ESS_WaveTest.pyz'),'gui','--port',str(port),'--no-browser'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
try:
 for i in range(70):
  try:
   text=urllib.request.urlopen(f'http://127.0.0.1:{port}/',timeout=1).read().decode();break
  except Exception:time.sleep(.1)
 else:raise RuntimeError('zipapp server not ready')
 token=re.search(r'window\.ESS_TOKEN\s*=\s*[\"\x27]([^\"\x27]+)',text)
 if not token:
  token=re.search(r'content="([A-Za-z0-9_\-]{35,})"',text)
 if not token:print(text[:800]);raise RuntimeError('no token')
 token=token.group(1)
 checks.append({'check':'zipapp contains and serves HTML resources','passed':'__ESS_TOKEN__' not in text})
 for name in ['app.js','timeline.js','style.css']:
  data=urllib.request.urlopen(f'http://127.0.0.1:{port}/'+name).read();checks.append({'check':'zipapp serves '+name,'passed':len(data)>100})
 def api(path,obj):
  r=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=json.dumps(obj).encode(),headers={'X-ESS-Token':token,'Content-Type':'application/json'})
  return json.loads(urllib.request.urlopen(r).read())
 d=api('/api/demo',{'variant':'pass'});r=api('/api/run',d['config'])
 checks.append({'check':'packaged server runs normal demo through real loopback HTTP','passed':r['status']=='PASS' and r['counts']['PASS']==6})
 req=urllib.request.Request(f'http://127.0.0.1:{port}/api/export/html',headers={'X-ESS-Token':token});body=urllib.request.urlopen(req).read()
 checks.append({'check':'packaged HTTP export contains self-contained report','passed':b'config_sha256' in body and b'<svg' in body})
 checks.append({'check':'packaged engine version is 1.2.0','passed':r['version']=='1.2.0'})
 d=api('/api/demo',{'variant':'C01_cycle_pass'});r=api('/api/run',d['config'])
 checks.append({'check':'packaged composite cycle runs all 18 assertions','passed':r['status']=='PASS' and r['counts']['PASS']==18})
 def get(path):
  return json.loads(urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}'+path,headers={'X-ESS-Token':token})).read())
 full=get('/api/trace?key=A1');zoom=get('/api/trace?key=A1&start=.3&end=.31')
 checks.append({'check':'packaged full overview reads 10001 samples','passed':full['source_samples']==10001 and full['mode']=='minmax'})
 checks.append({'check':'packaged zoom fetches original local samples','passed':zoom['source_samples']<15 and zoom['mode']=='samples'})
 # New noise path in the actual shipped zipapp (not source imports).
 from urllib.parse import urlencode
 for variant,expected in [('N01_white_noise','PASS'),('N02_emi_impulses','INCONCLUSIVE'),('N11_raw_safety_spike','FAIL'),('N15_contact_bounce','PASS')]:
  d=api('/api/demo',{'variant':variant});r=api('/api/run',d['config'])
  checks.append({'check':'packaged noise '+variant,'passed':r['status']==expected})
 d=api('/api/demo',{'variant':'N02_emi_impulses'});r=api('/api/run',d['config'])
 query={'key':'A1','test_id':'ROBUST_STEP','compare':'1','start':.825,'end':.84,
        'config_hash':r['config_sha256'],'record_hash':r['record']['hashes']['cfg_sha256']+r['record']['hashes']['dat_sha256']}
 z=get('/api/trace?'+urlencode(query))
 checks.append({'check':'packaged comparison zoom preserves raw spike and processed samples',
                'passed':max(v[1] for v in z['trace'])>120 and max(v[1] for v in z['processed_trace'])<102})
 checks.append({'check':'packaged comparison has two repair markers','passed':len(z['repair_markers'])==2})
 finally_text=''  
finally:
 process.terminate()
 try:process.communicate(timeout=5)
 except subprocess.TimeoutExpired:process.kill();process.communicate()
result={'mode':'single-file zipapp fresh subprocess real HTTP smoke test','checks':checks,'passed':sum(c['passed'] for c in checks),'total':len(checks)}
(root/'reports/release_smoke.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(result)
assert all(c['passed'] for c in checks)
