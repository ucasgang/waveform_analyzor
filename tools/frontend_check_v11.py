"""Chromium interactions -> bridge -> REAL loopback HTTP server.
Managed browser policy may block URL navigation. In that case only page loading
and transport are bridged; all API requests still exercise the shipped server.
This does not claim OS-level downloads or browser-direct localhost networking.
"""
from pathlib import Path
import base64,json,sys,threading,time,os,shutil,traceback
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from esswave.server import State,make_handler

OUT=ROOT/'reports/frontend_v11';OUT.mkdir(parents=True,exist_ok=True)
state=State();handler=make_handler(state);handler.log_message=lambda *a:None
server=ThreadingHTTPServer(('127.0.0.1',0),handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
BASE=f'http://127.0.0.1:{server.server_port}';checks=[];errors=[];requests=[];navigation='not attempted'

def bridge(source,path,payload,raw):
    body=base64.b64decode(payload) if raw else (payload.encode('utf-8') if payload is not None else None)
    req=Request(BASE+path,data=body,headers={'X-ESS-Token':state.token,'Content-Type':'application/octet-stream' if raw else 'application/json'})
    started=time.perf_counter()
    try:
        with urlopen(req,timeout=40) as r:answer={'status':r.status,'body':r.read().decode('utf-8'),'type':r.headers['Content-Type']}
    except HTTPError as e:answer={'status':e.code,'body':e.read().decode('utf-8'),'type':'application/json'}
    requests.append({'path':path,'status':answer['status'],'elapsed_ms':round((time.perf_counter()-started)*1000,3)})
    return answer

bridge_js='''<script>window.fetch=async function(path,options={}) {
 if(options.signal?.aborted)throw new DOMException('aborted','AbortError');
 let body=options.body??null;const raw=body instanceof Blob;
 if(raw){const bytes=new Uint8Array(await body.arrayBuffer());let s='';for(let i=0;i<bytes.length;i+=32768)s+=String.fromCharCode(...bytes.subarray(i,i+32768));body=btoa(s);}
 const delay=path.startsWith('/api/trace')?(window.testDelayTraceMs||0):0;if(delay)window.testDelayTraceMs=0;
 const r=await window.httpBridge(path,body,raw);if(delay)await new Promise(resolve=>setTimeout(resolve,delay));
 if(options.signal?.aborted)throw new DOMException('aborted','AbortError');
 return new Response(r.body,{status:r.status,headers:{'Content-Type':r.type}});
};</script>'''
html=(ROOT/'esswave/static/index.html').read_text().replace('__ESS_TOKEN__',state.token)
html=html.replace('<link rel="stylesheet" href="/style.css">','<style>'+(ROOT/'esswave/static/style.css').read_text()+'</style>')
html=html.replace('<script src="/timeline.js"></script>',bridge_js+'<script>'+(ROOT/'esswave/static/timeline.js').read_text()+'</script>')
html=html.replace('<script src="/plot.js"></script>','<script>'+(ROOT/'esswave/static/plot.js').read_text()+'</script>')
html=html.replace('<script src="/batch.js"></script>','<script>'+(ROOT/'esswave/static/batch.js').read_text()+'</script>')
html=html.replace('<script src="/app.js"></script>','<script>'+(ROOT/'esswave/static/app.js').read_text()+'</script>')

def check(name,passed,**evidence):
    checks.append({'check':name,'passed':bool(passed),**evidence});print(name, bool(passed),flush=True)
    if not passed:raise AssertionError(name)

def settled(page,selector='#previewChart'):
    page.wait_for_timeout(180)
    page.wait_for_function("sel => {const e=document.querySelector(sel);return e&&e.dataset.loading==='false';}",arg=selector,timeout=30000)

def bounds(page,selector='#previewChart'):
    return page.locator(selector).evaluate('(e)=>[Number(e.dataset.start),Number(e.dataset.end)]')

def precise(page,a,b,selector='#previewChart'):
    host=page.locator(selector);host.locator('[data-time=start]').fill(str(a));host.locator('[data-time=end]').fill(str(b));host.locator('button[type=submit]').click();settled(page,selector)

def demo(page,name):
    page.click('[data-tab=record]');page.select_option('#demoVariant',name);page.click('#loadDemo')
    page.wait_for_function("!document.getElementById('runTop').disabled",timeout=40000);settled(page)

try:
  with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('ESS_CHROMIUM') or shutil.which('chromium'),headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':1080},device_scale_factor=1)
    try:page.goto(BASE,wait_until='networkidle',timeout=10000);navigation='direct navigation available, fixture loaded below for controlled transport tests'
    except Exception as e:navigation=str(e).split('\n')[0]
    page.close();page=browser.new_page(viewport={'width':1440,'height':1080},device_scale_factor=1)
    page.on('pageerror',lambda e:errors.append(str(e)));page.expose_binding('httpBridge',bridge)
    page.set_content(html,wait_until='load');page.wait_for_function("document.querySelector('#configEditor').value.includes('schema_version')")
    demo(page,'C01_cycle_pass')
    check('复杂演示载入：12模拟+32数字+10001样本',page.locator('#analogCount').inner_text()=='12' and page.locator('#digitalCount').inner_text()=='32')
    page.locator('.preview').screenshot(path=str(OUT/'01_cycle_full.png'))
    precise(page,.3,.9);check('输入范围定位并重新读取601个左右原始采样',page.locator('#previewChart').get_attribute('data-mode')=='samples' and 600<=int(page.locator('#previewChart').get_attribute('data-samples'))<=603)
    page.locator('.preview').screenshot(path=str(OUT/'02_cycle_zoom.png'))
    frame=page.locator('#previewChart .timeline-frame');frame.scroll_into_view_if_needed();box=frame.bounding_box();old=bounds(page);anchor=old[0]+.25*(old[1]-old[0])
    page.mouse.move(box['x']+box['width']*(62+.25*921)/1000,box['y']+box['height']/2);page.mouse.wheel(0,-450);settled(page);new=bounds(page)
    print('WHEEL',old,new,anchor,new[0]+.25*(new[1]-new[0]),box,flush=True)
    check('滚轮以光标为锚点缩放',new[1]-new[0]<old[1]-old[0] and abs(new[0]+.25*(new[1]-new[0])-anchor)<(old[1]-old[0])/box['width']*1.1)
    old=new;box=frame.bounding_box();page.mouse.move(box['x']+box['width']*.6,box['y']+box['height']/2);page.mouse.down();page.mouse.move(box['x']+box['width']*.7,box['y']+box['height']/2,steps=8);page.mouse.up();settled(page);new=bounds(page)
    check('拖动平移保持视窗跨度',abs((new[1]-new[0])-(old[1]-old[0]))<1e-8 and new[0]<old[0])
    old=new;box=frame.bounding_box();page.keyboard.down('Shift');page.mouse.move(box['x']+box['width']*.25,box['y']+box['height']/2);page.mouse.down();page.mouse.move(box['x']+box['width']*.75,box['y']+box['height']/2,steps=8);page.mouse.up();page.keyboard.up('Shift');settled(page);new=bounds(page)
    check('Shift框选缩小时间范围',new[1]-new[0]<(old[1]-old[0])*.6)
    frame.dblclick();settled(page);check('双击复位完整10秒记录',bounds(page)==[0,10])
    page.locator('#previewChart [data-action=in]').click();settled(page);check('放大按钮将跨度减半',abs(bounds(page)[1]-bounds(page)[0]-5)<1e-8)
    nav=page.locator('#previewChart .timeline-nav');nb=nav.bounding_box();page.mouse.click(nb['x']+nb['width']*.9,nb['y']+10);settled(page);check('概览点击定位并限制在记录内',bounds(page)==[5,10])
    frame.focus();page.keyboard.press('Home');settled(page);check('键盘Home复位',bounds(page)==[0,10])
    old=bounds(page);host=page.locator('#previewChart');host.locator('[data-time=start]').fill('2');host.locator('[data-time=end]').fill('1');host.locator('button[type=submit]').click()
    check('拒绝颠倒时间范围且保留原视窗',bounds(page)==old and '无效' in host.locator('.timeline-status').inner_text())
    page.evaluate("() => {window.testDelayTraceMs=350;const v=[...WaveTimeline.instances].find(x=>x.host.id==='previewChart');v.setRange(.1,.2);v.fetchNow();setTimeout(()=>{v.setRange(.5,.51);v.fetchNow();},20);}")
    page.wait_for_timeout(600);settled(page);check('乱序旧请求不能覆盖新窗口',all(abs(a-b)<1e-8 for a,b in zip(bounds(page),[.5,.51])) and int(host.get_attribute('data-samples'))<15)
    page.evaluate("() => {window.testDelayTraceMs=300;preview('A1');setTimeout(()=>preview('D1'),20);}");page.wait_for_timeout(600);settled(page)
    check('切换通道时取消旧视图与旧请求',page.locator('#previewTitle').inner_text()=='调度命令脉冲' and 'D1' in page.locator('#previewUnit').inner_text())
    page.click('#runTop');page.wait_for_selector('.resultbanner.PASS');page.wait_for_function("[...document.querySelectorAll('.result-timeline')].every(x=>x.dataset.loading==='false')")
    check('复杂C01在界面执行18项断言且每张证据图可缩放',page.locator('.resultcard').count()==18 and page.locator('.result-timeline').count()==18)
    oldresult=json.dumps(state.result,sort_keys=True)
    precise(page,.5,.8,'.result-timeline[data-test-id=P1]')
    check('结果证据图缩放不改变分析结果与配置快照',json.dumps(state.result,sort_keys=True)==oldresult)
    page.locator('.resultcard').first.screenshot(path=str(OUT/'03_result_zoom.png'))
    demo(page,'C06_multirate_skew');page.click('#runTop');page.wait_for_selector('.resultbanner.PASS');page.wait_for_function("[...document.querySelectorAll('.result-timeline')].every(x=>x.dataset.loading==='false')")
    scaled=page.evaluate("() => {const v=[...WaveTimeline.instances].find(v=>v.host.dataset.testId==='SKEW_STEP');return {unit:v.data.unit,max:Math.max(...v.data.trace.map(p=>p[1]))};}")
    check('结果图正确显示一次侧换算后的kW而非原始W',scaled['unit']=='kW' and 99<scaled['max']<101,measurement=scaled)
    demo(page,'C09_dense_512');page.evaluate("preview('D300')");settled(page)
    check('512通道全览对密集数字变位显示密度包络',page.locator('#previewChart').get_attribute('data-mode')=='digital_envelope' and page.locator('#previewChart .digital-density').count()>0)
    page.locator('.preview').screenshot(path=str(OUT/'04_dense_envelope.png'))
    precise(page,1.234,1.237);check('放大后从包络恢复数字原始边沿',page.locator('#previewChart').get_attribute('data-mode')=='samples' and 30<=int(page.locator('#previewChart').get_attribute('data-samples'))<=33)
    page.locator('.preview').screenshot(path=str(OUT/'05_dense_edges.png'))
    page.evaluate("preview('A128')");settled(page);precise(page,1.234,1.236)
    peak=page.evaluate("()=>Math.max(...[...WaveTimeline.instances].find(x=>x.host.id==='previewChart').data.trace.map(p=>p[1]))")
    check('放大后显示1.2345s的125V单采样尖峰',peak==125)
    page.locator('.preview').screenshot(path=str(OUT/'06_single_sample_spike.png'))
    page.click('#runTop');page.wait_for_selector('.resultbanner.FAIL');page.wait_for_function("[...document.querySelectorAll('.result-timeline')].every(x=>x.dataset.loading==='false')",timeout=40000)
    check('一万脉冲检查限制界面行数但保留全部结果',max(page.locator('.resultcard').nth(i).locator('tbody tr').count() for i in range(page.locator('.resultcard').count()))<=200 and len(next(r for r in state.result['results'] if r['id']=='HIGH_FREQUENCY')['checks'])==10001)
    demo(page,'C11_selected_missing');page.evaluate("preview('A2')");settled(page);precise(page,.8,.804)
    nulls=page.evaluate("()=>[...WaveTimeline.instances].find(x=>x.host.id==='previewChart').data.trace.filter(p=>p[1]===null).length")
    path=page.locator('#previewChart .timeline-svg .wave-data').get_attribute('d')
    check('缺失样本断线而不跨缺失区间连线',nulls>0 and path.count('M')>=2)
    page.locator('.preview').screenshot(path=str(OUT/'07_missing_break.png'))
    demo(page,'gap');check('采样间隙显示灰色区域',page.locator('#previewChart .sampling-gap').count()>0)
    page.set_viewport_size({'width':390,'height':844});page.locator('.preview').scroll_into_view_if_needed();page.screenshot(path=str(OUT/'08_mobile.png'),full_page=True)
    check('390px移动布局无横向溢出',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
    page.set_viewport_size({'width':1440,'height':1080});demo(page,'C02_cycle_race');page.click('#runTop');page.wait_for_selector('.resultbanner.FAIL')
    page.evaluate("() => {window.saveBlob=async function(blob,name){window.testExport={name,text:await blob.text()};};}")
    page.click('[data-export=html]');page.wait_for_function('window.testExport!==undefined');data=page.evaluate('window.testExport')
    check('报告按钮通过真实HTTP生成当前版本报告正文',__import__('esswave').__version__ in data['text'] and 'ACK2' in data['text'])
    check('无未处理JavaScript异常',not errors,errors=errors)
    browser.close()
except Exception:
    traceback.print_exc()
    try:page.screenshot(path=str(OUT/'failure.png'),full_page=True)
    except Exception:pass
    checks.append({'check':'test_runner_completed','passed':False,'error':traceback.format_exc()})
finally:
    server.shutdown();server.server_close();thread.join();state.close()
    result={'mode':'Chromium DOM interactions + Python transport bridge to real loopback HTTP server','direct_navigation':navigation,
            'limitations':'未验证浏览器直接连接localhost及操作系统级下载；没有更改浏览器管理策略。',
            'total':len(checks),'passed':sum(x['passed'] for x in checks),'checks':checks,'http_requests':len(requests),'requests':requests,'errors':errors}
    (ROOT/'reports/browser_results_v11.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('TOTAL',result['passed'],'/',result['total'])
raise SystemExit(0 if checks and all(x['passed'] for x in checks) else 1)
