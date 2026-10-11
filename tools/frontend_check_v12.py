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

OUT=ROOT/'reports/frontend_v12';OUT.mkdir(parents=True,exist_ok=True)
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


def run_ui(page,k):
    demo(page,k);page.click('#runTop')
    page.wait_for_function("!document.getElementById('runTop').disabled",timeout=40000)
    page.wait_for_function("[...document.querySelectorAll('.result-timeline')].every(x=>x.dataset.loading==='false')",timeout=40000)

try:
  with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('ESS_CHROMIUM') or shutil.which('chromium'),headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
    try:
        page.goto(BASE,wait_until='networkidle',timeout=7000)
        navigation='direct navigation succeeded; controlled interaction transport uses bridge below'
    except Exception as e:navigation=str(e).split('\n')[0]
    page.close();page=browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
    page.on('pageerror',lambda e:errors.append(str(e)));page.expose_binding('httpBridge',bridge)
    page.set_content(html,wait_until='load');page.wait_for_function("document.querySelector('#configEditor').value.includes('schema_version')")
    check('32组噪声演示出现在下拉框',page.locator('#demoVariant option[value^=N]').count()==32)
    check('页脚版本1.3.0', '1.3.0' in page.locator('footer').inner_text())
    run_ui(page,'N01_white_noise')
    check('健康噪声工况在界面通过',page.locator('.resultbanner.PASS').count()==1)
    check('结果卡包含双分支说明',page.locator('.noise-evidence').count()==1)
    check('原始和处理曲线同时绘制',page.locator('.result-timeline .timeline-svg .wave-data').count()==1 and page.locator('.result-timeline .wave-processed').count()==1)
    run_ui(page,'N02_emi_impulses')
    check('EMI尖峰不能被静默判为PASS',page.locator('.resultbanner.INCONCLUSIVE').count()==1)
    page.locator('.resultcard').first.screenshot(style='#toast{visibility:hidden!important}',path=str(OUT/'01_impulse_review.png'))
    sel='.result-timeline';precise(page,.825,.84,sel)
    data=page.evaluate("()=>[...WaveTimeline.instances].find(x=>x.host.classList.contains('result-timeline')).data")
    check('放大后读取局部完整原始/处理采样',data['source_samples']<=18 and data['processed_source_samples']<=18)
    check('原始双点尖峰仍超过120kW',max(x[1] for x in data['trace'])>120)
    check('处理曲线回到约100kW',max(x[1] for x in data['processed_trace'])<102)
    check('两个候选修复点可见',page.locator('.repair-marker').count()==2)
    page.locator('.resultcard').first.screenshot(style='#toast{visibility:hidden!important}',path=str(OUT/'02_spike_zoom.png'))
    before=json.dumps(state.result,sort_keys=True)
    page.locator('[data-layer=processed]').uncheck()
    check('可以隐藏处理曲线但保留原始曲线',page.locator('.wave-processed').count()==0 and page.locator('.result-timeline .timeline-svg .wave-data').count()==1)
    page.locator('[data-layer=processed]').check();page.locator('[data-layer=raw]').uncheck()
    check('可以单独查看处理曲线',page.locator('.wave-processed').count()==1 and page.locator('.result-timeline .timeline-svg .wave-data').count()==0)
    page.locator('[data-layer=raw]').check();page.locator('[data-layer=repairs]').uncheck()
    check('候选标记可单独隐藏',page.locator('.repair-marker').count()==0)
    page.locator('[data-layer=repairs]').check()
    check('图层切换不修改任何判定或配置',before==json.dumps(state.result,sort_keys=True))
    page.locator('.noise-comparison summary').click()
    check('原始/处理指标对比表可展开',page.locator('.noise-comparison[open] tbody tr').count()==6)
    run_ui(page,'N15_contact_bounce');precise(page,.49,.53,sel)
    check('非对称数字去抖通过',page.locator('.resultbanner.PASS').count()==1)
    check('被抑制的抖动区间仍被标出',page.locator('.rejected-span').count()>0)
    check('数字原始和处理两条阶梯线均显示',page.locator('.result-timeline .timeline-svg .wave-data').count()==1 and page.locator('.wave-processed').count()==1)
    page.locator('.resultcard').first.screenshot(style='#toast{visibility:hidden!important}',path=str(OUT/'03_contact_bounce.png'))
    run_ui(page,'N16_chatter_storm')
    check('抖动风暴质量预算阻止自动通过',page.locator('.resultbanner.INCONCLUSIVE').count()==1 and '最长抖动' in page.locator('.noise-evidence').inner_text())
    run_ui(page,'N11_raw_safety_spike');precise(page,.79,.81,sel)
    check('单采样原始硬限值在界面仍为FAIL',page.locator('.resultbanner.FAIL').count()==1 and '原始最大值' in page.locator('.resultcard .resulttable').inner_text())
    page.locator('.resultcard').first.screenshot(style='#toast{visibility:hidden!important}',path=str(OUT/'04_raw_safety_veto.png'))
    run_ui(page,'N06_burst_noise')
    check('强噪声工况不给出自动性能结论',page.locator('.resultbanner.INCONCLUSIVE').count()==1)
    page.locator('.resultcard').first.screenshot(style='#toast{visibility:hidden!important}',path=str(OUT/'05_noise_budget.png'))
    run_ui(page,'N21_noisy_analog_trigger')
    check('滞回与保持确认后的模拟触发工况通过',page.locator('.resultbanner.PASS').count()==1)
    run_ui(page,'N18_invalid_debounce')
    check('吞保护脉冲的配置被拒绝且不保留旧报告', '合法保护脉冲' in page.locator('#toast').inner_text() and page.locator('#exportButtons.hidden').count()==1)
    run_ui(page,'N02_emi_impulses')
    page.evaluate("() => {window.saveBlob=async function(blob,name){window.testExport={name,text:await blob.text()};};}")
    page.click('[data-export=html]');page.wait_for_function('window.testExport!==undefined');exported=page.evaluate('window.testExport')
    check('导出HTML包含双分支审计与版本', '原始与处理双分支' in exported['text'] and '1.3.0' in exported['text'])
    page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(150)
    check('390px结果页无横向溢出',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
    page.screenshot(style='#toast{visibility:hidden!important}',path=str(OUT/'06_mobile_results.png'),full_page=True)
    page.click('[data-tab=record]');page.wait_for_timeout(150)
    check('390px长场景名不撑破录波页',page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
    page.set_viewport_size({'width':1440,'height':1100});run_ui(page,'N32_many_channels')
    check('16模拟512数字的含噪场景通过',page.locator('#analogCount').inner_text()=='16' and page.locator('#digitalCount').inner_text()=='512' and page.locator('.resultbanner.PASS').count()==1)
    page.click('[data-tab=rules]');check('抗噪配置提示可在界面找到',page.locator('.noise-config').count()==1)
    check('没有未捕获的JavaScript异常',not errors,errors=errors)
    browser.close()
except Exception:
    traceback.print_exc()
    try:page.screenshot(style='#toast{visibility:hidden!important}',path=str(OUT/'failure.png'),full_page=True)
    except Exception:pass
    checks.append({'check':'test_runner_completed','passed':False,'error':traceback.format_exc()})
finally:
    server.shutdown();server.server_close();thread.join();state.close()
    result={'version':'1.3.0','mode':'Chromium DOM interactions + Python bridge to real loopback HTTP', 'direct_navigation':navigation,
            'limitations':'浏览器页面装载和fetch由可审计转发桥接；未把该测试当作直接localhost导航或操作系统级下载测试。',
            'total':len(checks),'passed':sum(x['passed'] for x in checks),'checks':checks,'http_requests':len(requests),'requests':requests,'errors':errors}
    (ROOT/'reports/browser_results_v12.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('TOTAL',result['passed'],'/',result['total'])
raise SystemExit(0 if checks and all(x['passed'] for x in checks) else 1)
