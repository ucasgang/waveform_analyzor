"""Real Chromium folder input, local HTTP, zoom and OFFLINE HTML report checks.
Uses normal browser navigation when allowed; no fabricated screenshots.
"""
from pathlib import Path
import json,sys,threading,time,tempfile,shutil,traceback,zipfile,base64
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from esswave.server import State,make_handler
from esswave.generate import scenario
OUT=ROOT/'reports/frontend_v13';OUT.mkdir(parents=True,exist_ok=True)
state=State();handler=make_handler(state);handler.log_message=lambda *a:None
server=ThreadingHTTPServer(('127.0.0.1',0),handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
BASE=f'http://127.0.0.1:{server.server_port}';checks=[];errors=[];requests=[]
tmp=tempfile.TemporaryDirectory();fixtures=Path(tmp.name)/'批次样本';fixtures.mkdir()
# Same basename in different subdirectories must NEVER pair across directories.
for sub,variant in [('A_正常','pass'),('B_异常','overshoot')]:
    scenario(fixtures/sub,'录波',variant=variant)
(fixtures/'缺文件.cfg').write_text('broken')
(fixtures/'孤立.DAT').write_bytes(b'orphan')

def check(name,value,**details):
    checks.append({'name':name,'passed':bool(value),**details});print(name,bool(value),flush=True)
    if not value:raise AssertionError(name)

def settled(page,selector):
    page.wait_for_timeout(160);page.wait_for_function("s=>{const e=document.querySelector(s);return e&&e.dataset.loading==='false';}",arg=selector,timeout=30000)

def rng(page,selector):return page.locator(selector).evaluate('e=>[Number(e.dataset.start),Number(e.dataset.end)]')
def precise(page,selector,a,b):
    h=page.locator(selector);h.locator('[data-time=start]').fill(str(a));h.locator('[data-time=end]').fill(str(b));h.locator('button[type=submit]').click();settled(page,selector)

def zoom_checks(page,selector,label):
    h=page.locator(selector);settled(page,selector);full=rng(page,selector)
    check(label+'：放大缩小按钮可见',h.locator('[data-action=in]').is_visible() and h.locator('[data-action=out]').is_visible())
    h.locator('[data-action=in]').click();settled(page,selector);narrow=rng(page,selector)
    check(label+'：按钮放大改变真实时间范围',narrow[1]-narrow[0]<(full[1]-full[0])*.6)
    h.locator('[data-action=out]').click();settled(page,selector);check(label+'：按钮缩小恢复',abs((rng(page,selector)[1]-rng(page,selector)[0])-(full[1]-full[0]))<1e-8)
    frame=h.locator('.timeline-frame');frame.scroll_into_view_if_needed();frame.hover();page.mouse.wheel(0,-450);settled(page,selector)
    check(label+'：滚轮缩放',rng(page,selector)[1]-rng(page,selector)[0]<full[1]-full[0])
    h.locator('[data-action=reset]').click();settled(page,selector)
    precise(page,selector,full[0]+.1*(full[1]-full[0]),full[0]+.2*(full[1]-full[0]))
    check(label+'：精确时间定位',abs(rng(page,selector)[0]-(full[0]+.1*(full[1]-full[0])))<1e-8)
    before=frame.locator('svg').inner_html();h.locator('[data-action=yin]').click();check(label+'：幅值轴独立放大',frame.locator('svg').inner_html()!=before)
    h.locator('[data-action=yreset]').click();h.locator('[data-action=reset]').click();settled(page,selector)
    box=frame.bounding_box();page.keyboard.down('Shift');page.mouse.move(box['x']+box['width']*.25,box['y']+60);page.mouse.down();page.mouse.move(box['x']+box['width']*.55,box['y']+60,steps=6);page.mouse.up();page.keyboard.up('Shift');settled(page,selector)
    check(label+'：Shift框选放大',rng(page,selector)[1]-rng(page,selector)[0]<(full[1]-full[0])*.5)
    before=rng(page,selector);box=frame.bounding_box();page.mouse.move(box['x']+box['width']*.65,box['y']+60);page.mouse.down();page.mouse.move(box['x']+box['width']*.55,box['y']+60,steps=5);page.mouse.up();settled(page,selector)
    check(label+'：拖动平移',rng(page,selector)[0]!=before[0])
    frame.dblclick();settled(page,selector);check(label+'：双击复位',abs(rng(page,selector)[0]-full[0])<1e-8)

def bridge(source,path,payload,raw):
    body=base64.b64decode(payload) if raw else (payload.encode('utf-8') if payload is not None else None)
    req=Request(BASE+path,data=body,headers={'X-ESS-Token':state.token,'Content-Type':'application/octet-stream' if raw else 'application/json'})
    try:
        with urlopen(req,timeout=90) as r:answer={'status':r.status,'body':base64.b64encode(r.read()).decode(),'type':r.headers['Content-Type']}
    except HTTPError as e:answer={'status':e.code,'body':base64.b64encode(e.read()).decode(),'type':'application/json'}
    return answer

bridge_js='''<script>window.fetch=async function(path,options={}) {
 if(options.signal?.aborted)throw new DOMException('aborted','AbortError');
 let body=options.body??null;const raw=body instanceof Blob;
 if(raw){const bytes=new Uint8Array(await body.arrayBuffer());let s='';for(let i=0;i<bytes.length;i+=32768)s+=String.fromCharCode(...bytes.subarray(i,i+32768));body=btoa(s);}
 const r=await window.httpBridge(path,body,raw);
 if(options.signal?.aborted)throw new DOMException('aborted','AbortError');
 return new Response(Uint8Array.from(atob(r.body),c=>c.charCodeAt(0)),{status:r.status,headers:{'Content-Type':r.type}});
};</script>'''

def bridged_page(page):
    html=(ROOT/'esswave/static/index.html').read_text().replace('__ESS_TOKEN__',state.token)
    html=html.replace('<link rel="stylesheet" href="/style.css">','<style>'+(ROOT/'esswave/static/style.css').read_text()+'</style>')
    for name in ('plot','timeline','app','batch'):
        html=html.replace('<script src="/'+name+'.js"></script>',(bridge_js if name=='plot' else '')+'<script>'+(ROOT/('esswave/static/'+name+'.js')).read_text()+'</script>')
    page.expose_binding('httpBridge',bridge);page.set_content(html,wait_until='load')
    page.wait_for_function('document.querySelector("#configEditor").value.includes("schema_version")')

navigation='not_started';browser=None;navigation_error='';download_mode='not_tested';offline_mode='not_tested'
try:
 with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
    context=browser.new_context(viewport={'width':1480,'height':1080},accept_downloads=True)
    page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:requests.append(r.url))
    try:
        response=page.goto(BASE,wait_until='networkidle',timeout=20000);navigation='direct_loopback_http'
        check('真实浏览器直接访问本机工作台',response.status==200 and page.locator('[data-tab=batch]').count()==1)
    except Exception as exc:
        navigation_error=str(exc).split('\n')[0];navigation='Chromium DOM + binary-safe bridge to real loopback HTTP'
        page.close();page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        bridged_page(page);check('受管浏览器桥接真实本机API并装载批处理页',page.locator('[data-tab=batch]').count()==1)

    page.click('[data-tab=batch]');page.locator('#batchFolder').set_input_files(str(fixtures))
    check('文件夹选择保留子目录相对路径',page.locator('#batchFolder').evaluate('e=>Array.from(e.files).some(f=>f.webkitRelativePath.includes("A_正常/录波.cfg"))'))
    page.click('#batchScan');page.wait_for_function('batchState&&batchState.total===4')
    check('4组扫描含两组错误而非跳过',page.evaluate('batchState.counts.ERROR===2&&batchState.rows.filter(r=>r.status==="WAITING").length===2'))
    page.screenshot(path=str(OUT/'01_folder_scan.png'),full_page=True,style='#toast{visibility:hidden!important}')
    page.click('#batchStart');page.wait_for_function('batchState.state==="COMPLETE"',timeout=60000)
    check('实际批次PASS1/FAIL1/ERROR2',page.evaluate('batchState.counts.PASS===1&&batchState.counts.FAIL===1&&batchState.counts.ERROR===2'))
    check('错误记录没有被标记为可打开结果',page.locator('[data-batch-open]').count()==2)
    page.screenshot(path=str(OUT/'02_folder_complete.png'),full_page=True,style='#toast{visibility:hidden!important}')
    saved=page.evaluate('JSON.stringify(batchState)')
    page.select_option('#batchFilter','FAIL');check('失败筛选只显示一组',page.locator('#batchRows tr').count()==1)
    page.select_option('#batchFilter','all');page.locator('[data-batch-open]').first.click();page.wait_for_selector('.result-timeline');page.wait_for_function('!busy')
    check('批次详情绑定保存的结果',page.locator('.resultbanner.PASS').count()==1)
    zoom_checks(page,'.result-timeline[data-test-id="D01"]','批量详情')
    # Selector has six matches; use first via unique data-test id.
    check('查看不改变批次快照',saved==page.evaluate('JSON.stringify(batchState)'))
    page.click('[data-tab=record]');page.locator('[data-preview=A1]').click();zoom_checks(page,'#previewChart','录波预览')
    page.click('[data-tab=batch]')
    archive=OUT/'downloaded_reports.zip'
    # Capture the real exported Blob too: managed policy may block all OS downloads.
    page.evaluate('''() => {const original=window.saveBlob;window.saveBlob=async function(blob,name){const bytes=new Uint8Array(await blob.arrayBuffer());let s='';for(let i=0;i<bytes.length;i+=32768)s+=String.fromCharCode(...bytes.subarray(i,i+32768));window.testBatchExport={name,base64:btoa(s)};original(blob,name);};}''')
    try:
        with page.expect_download(timeout=7000) as info:page.click('#batchExport')
        info.value.save_as(archive);download_mode='real browser download'
    except Exception as exc:
        page.wait_for_function('window.testBatchExport!==undefined',timeout=30000)
        archive.write_bytes(base64.b64decode(page.evaluate('testBatchExport.base64')));download_mode='exported Blob verified; OS download blocked: '+str(exc).split('\n')[0]

    with zipfile.ZipFile(archive) as z:
        check('实际导出ZIP包含索引且无原始DAT重复副本','index.html' in z.namelist() and not any(n.endswith('.dat') for n in z.namelist()))
        z.extractall(OUT/'downloaded_reports')
    report=OUT/'downloaded_reports/items/r00001/out/report.html'
    standalone=context.new_page();standalone.on('pageerror',lambda e:errors.append(str(e)));external=[];standalone.on('request',lambda r:external.append(r.url))
    try:
        standalone.goto(report.as_uri(),wait_until='load');offline_mode='direct file URL'
    except Exception:
        standalone.close();standalone=context.new_page();standalone.on('pageerror',lambda e:errors.append(str(e)));standalone.on('request',lambda r:external.append(r.url))
        standalone.set_content(report.read_text(),wait_until='load');offline_mode='exact exported HTML loaded via set_content; file navigation blocked'
    selector='.report-wave[data-wave-id="0"]';settled(standalone,selector)

    check('下载报告离线打开内嵌完整窗口采样',standalone.locator(selector).get_attribute('data-resolution')=='full')
    zoom_checks(standalone,selector,'离线HTML报告')
    check('离线HTML没有外部网络依赖',not any(u.startswith(('http://','https://')) for u in external))
    standalone.screenshot(path=str(OUT/'03_offline_report_zoom.png'),full_page=False)
    # Large multi-channel scene and explicit single-sample protection view.
    page.click('[data-tab=record]');page.select_option('#demoVariant','F05_trip_1ms');page.click('#loadDemo');page.wait_for_function('!busy',timeout=40000)
    page.locator('[data-preview=D5]').click();settled(page,'#previewChart');precise(page,'#previewChart',6.558,6.563)
    data=page.evaluate('[...WaveTimeline.instances].find(t=>t.host.id==="previewChart").data')
    check('复合背景下1ms保护脉冲可放大到原始采样',data['mode']=='samples' and any(p[1]==1 for p in data['trace']) and data['source_samples']<20)
    page.locator('.preview').screenshot(path=str(OUT/'04_protection_pulse_zoom.png'),style='#toast{visibility:hidden!important}')
    page.locator('[data-preview=A11]').click();settled(page,'#previewChart');precise(page,'#previewChart',6.40,6.83)
    page.locator('.preview').screenshot(path=str(OUT/'05_phase_sag.png'),style='#toast{visibility:hidden!important}')
    page.set_viewport_size({'width':390,'height':844});page.click('[data-tab=batch]');page.wait_for_timeout(200)
    check('移动窄屏批处理页不撑破视窗',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
    page.screenshot(path=str(OUT/'06_mobile_batch.png'),full_page=True,style='#toast{visibility:hidden!important}')
    check('没有未捕获JavaScript异常',not errors,errors=errors)
    browser.close();browser=None
except Exception:
    traceback.print_exc()
    try:page.screenshot(path=str(OUT/'failure.png'),full_page=True)
    except Exception:pass
    checks.append({'name':'runner_completed','passed':False,'error':traceback.format_exc()})
finally:
    if browser:
        try:browser.close()
        except Exception:pass
    server.shutdown();server.server_close();thread.join();state.close();tmp.cleanup()
    result={'version':'1.3.0','navigation':navigation,'navigation_error':navigation_error,'download_mode':download_mode,'offline_mode':offline_mode,'total':len(checks),'passed':sum(x['passed'] for x in checks),'checks':checks,'errors':errors,'requests':requests}
    (OUT/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('TOTAL',result['passed'],'/',result['total'])
raise SystemExit(0 if checks and all(x['passed'] for x in checks) else 1)
