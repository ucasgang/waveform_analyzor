"""Loopback-only browser workbench. No outbound requests, eval or shell execution."""
from __future__ import annotations
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
import json
from pathlib import Path
import secrets
import struct
import tempfile
import threading
from urllib.parse import urlparse, parse_qs
import webbrowser
from .comtrade import load, ComtradeError
from .engine import run, ConfigError, compact_trace
from .generate import scenario,demo_suite
from .trace import trace_window
from .complex_cases import generate_case,CATALOG
from .noise_cases import generate_case as generate_noise_case,CATALOG as NOISE_CATALOG
from .noise_view import processed_record
from .field_cases import generate_case as generate_field_case,CATALOG as FIELD_CATALOG
from .report import html_report,csv_text,junit_text
from .batchwork import BatchManager,strict_json
import shutil

MAX_UPLOAD=128*1024**2

class State:
    def __init__(self):
        self.token=secrets.token_urlsafe(32);self.record=None;self.result=None
        self.processed_cache={};self.lock=threading.Lock();self.temp=tempfile.TemporaryDirectory(prefix="esswave_")
        self.batch=BatchManager(Path(self.temp.name)/"batches")
    def close(self):self.batch.close();self.temp.cleanup()


def make_handler(state:State):
    class Handler(BaseHTTPRequestHandler):
        server_version="ESSWaveTest/1.3"
        def log_message(self,fmt,*args):
            # Do not log uploaded content, tokens or full query strings.
            print(f"[HTTP] {self.command} {urlparse(self.path).path} {args[1] if len(args)>1 else ''}")
        def setup(self):
            super().setup();self.connection.settimeout(40)
        def allowed(self,api=False):
            port=self.server.server_port
            hosts={f"127.0.0.1:{port}",f"localhost:{port}"}
            if self.headers.get("Host","") not in hosts:
                self.reply({"error":"拒绝非本机 Host"},403);return False
            origin=self.headers.get("Origin")
            if origin and origin not in {"http://"+h for h in hosts}:
                self.reply({"error":"拒绝跨域访问"},403);return False
            if api and not hmac.compare_digest(self.headers.get("X-ESS-Token",""),state.token):
                self.reply({"error":"会话令牌失效，请刷新当前页面"},403);return False
            return True
        def reply(self,body,status=200,ctype="application/json; charset=utf-8",filename=None):
            raw=json.dumps(body,ensure_ascii=False,allow_nan=False).encode("utf-8") if isinstance(body,(dict,list)) else (body.encode("utf-8") if isinstance(body,str) else body)
            self.send_response(status);self.send_header("Content-Type",ctype);self.send_header("Content-Length",str(len(raw)))
            self.send_header("X-Content-Type-Options","nosniff");self.send_header("Cache-Control","no-store")
            self.send_header("Referrer-Policy","no-referrer");self.send_header("X-Frame-Options","DENY")
            self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
            if filename:self.send_header("Content-Disposition",f'attachment; filename="{filename}"')
            self.end_headers();self.wfile.write(raw)
        def body(self,limit=2*1024**2):
            if self.headers.get("Transfer-Encoding"):raise ValueError("不接受分块传输请求")
            try:n=int(self.headers.get("Content-Length","0"))
            except ValueError:raise ValueError("无效 Content-Length")
            if not 0<n<=limit:raise ValueError(f"请求体须为 1..{limit} 字节")
            raw=self.rfile.read(n)
            if len(raw)!=n:raise ValueError("上传中断")
            return raw
        def do_GET(self):
            path=urlparse(self.path).path
            if not self.allowed(path.startswith("/api/")):return
            try:
                if path in ("/","/index.html"):
                    text=resources.files("esswave").joinpath("static/index.html").read_text(encoding="utf-8")
                    self.reply(text.replace("__ESS_TOKEN__",state.token),ctype="text/html; charset=utf-8")
                elif path in ("/app.js","/timeline.js","/plot.js","/batch.js","/style.css"):
                    text=resources.files("esswave").joinpath("static"+path).read_bytes()
                    self.reply(text,ctype="application/javascript; charset=utf-8" if path.endswith("js") else "text/css; charset=utf-8")
                elif path=="/api/template":self.reply(demo_suite())
                elif path=="/api/batch/current":self.reply(state.batch.snapshot())
                elif path.startswith("/api/batch/status/"):self.reply(state.batch.snapshot(path.rsplit("/",1)[1]))
                elif path.startswith("/api/batch/export/"):
                    filename=state.batch.export_zip(path.rsplit("/",1)[1])
                    self.send_response(200);self.send_header("Content-Type","application/zip")
                    self.send_header("Content-Length",str(filename.stat().st_size));self.send_header("Cache-Control","no-store")
                    self.send_header("X-Content-Type-Options","nosniff");self.send_header("Content-Disposition",'attachment; filename="ESS_batch_reports.zip"')
                    self.end_headers()
                    with filename.open("rb") as source:shutil.copyfileobj(source,self.wfile,1024*1024)
                elif path=="/api/trace":
                    with state.lock:
                        if state.record is None:raise ValueError("请先加载录波")
                        q=parse_qs(urlparse(self.path).query,keep_blank_values=True)
                        if set(q)-{"key","start","end","limit","record_hash","test_id","config_hash","compare"}:
                            raise ValueError("未知波形查询参数")
                        if any(len(v)!=1 for v in q.values()):raise ValueError("查询参数不能重复")
                        def value(k,default=None):return q.get(k,[default])[0]
                        record_hash=state.record.hashes["cfg_sha256"]+state.record.hashes["dat_sha256"]
                        if value("record_hash") is not None and value("record_hash")!=record_hash:
                            raise ValueError("录波已更换，请刷新视图")
                        key=value("key","");options={};item=None
                        if value("compare","0") not in ("0","1"):raise ValueError("compare 只接受 0/1")
                        if value("test_id") is not None:
                            if state.result is None:raise ValueError("请重新运行断言")
                            if value("config_hash")!=state.result["config_sha256"]:raise ValueError("结果已更换，请刷新视图")
                            item=next((a for a in state.result["results"] if a["id"]==value("test_id")),None)
                            if item is None or not item["evidence"].get("mapping"):raise ValueError("此断言没有可显示的波形证据")
                            e=item["evidence"];m=e["mapping"]
                            key=m["key"]
                            options={"factor":m["factor_including_ratio"],"offset":m["offset"],
                                     "output_unit":m["output_unit"],"bounds":e["window_absolute_s"]}
                        start=float(value("start")) if "start" in q else None
                        end=float(value("end")) if "end" in q else None
                        limit=int(value("limit",1400))
                        payload=trace_window(state.record,key,start,end,limit,**options)
                        if value("compare")=="1":
                            if item is None:raise ValueError("对比必须绑定已运行的断言")
                            cache_key=state.result["config_sha256"]+":"+item["id"]
                            cached=state.processed_cache.get(cache_key)
                            if cached is None:
                                cached=processed_record(state.record,state.result["configuration"],item)
                                while state.processed_cache and (len(state.processed_cache)>=4 or sum(len(v[0].time) for v in state.processed_cache.values())+len(cached[0].time)>250000):
                                    state.processed_cache.pop(next(iter(state.processed_cache)))
                                if len(cached[0].time)<=250000:state.processed_cache[cache_key]=cached
                            view,markers,rejected=cached
                            processed=trace_window(view,key,start,end,limit,bounds=options["bounds"])
                            visible=[p for p in markers if payload["start_s"]<=p["time_s"]<=payload["end_s"]]
                            spans=[p for p in rejected if p["start_s"]<payload["end_s"] and p["end_s"]>payload["start_s"]]
                            payload.update(processed_trace=processed["trace"],processed_bins=processed["digital_bins"],
                                           processed_source_samples=processed["source_samples"],processed_mode=processed["mode"],
                                           repair_markers=visible[:256],repair_marker_count=len(visible),rejected_spans=spans[:256])
                            payload["warning"]+="；蓝线原始 / 橙线已按本次用例处理；圆点为修复候选，不证明是噪声"
                        self.reply(payload)
                elif path.startswith("/api/export/"):
                    with state.lock:
                        r=state.result
                        if r is None:raise ValueError("请先运行断言")
                        ext=path.rsplit("/",1)[1]
                        if ext=="html":self.reply(html_report(r,record=state.record),ctype="text/html; charset=utf-8",filename="report.html")
                        elif ext=="json":self.reply(r,filename="result.json")
                        elif ext=="csv":self.reply(csv_text(r),ctype="text/csv; charset=utf-8",filename="metrics.csv")
                        elif ext=="xml":self.reply(junit_text(r),ctype="application/xml; charset=utf-8",filename="junit.xml")
                        else:self.reply({"error":"不支持的导出格式"},404)
                else:self.reply({"error":"未找到"},404)
            except (ValueError,OSError) as exc:self.reply({"error":str(exc)},400)
        def do_POST(self):
            if not self.allowed(True):return
            path=urlparse(self.path).path
            try:
                # Batch operations use a separate lock; status/cancel remain responsive
                # while a worker performs full-sample analysis.
                if path.startswith("/api/batch/"):
                    parts=path.strip("/").split("/");action=parts[2] if len(parts)>2 else ""
                    if action=="create" and len(parts)==3:
                        a=strict_json(self.body(4*1024**2))
                        if not isinstance(a,dict):raise ValueError("批处理请求必须为 JSON 对象")
                        if set(a)-{"files","mode","config","recursive"}:raise ValueError("未知批处理参数")
                        self.reply(state.batch.create(a.get("files"),a.get("mode","sibling"),a.get("config"),a.get("recursive",True)))
                    elif action=="upload" and len(parts)==5:self.reply(state.batch.upload(parts[3],parts[4],self.body(MAX_UPLOAD)))
                    elif action in ("start","cancel") and len(parts)==4:
                        a=strict_json(self.body())
                        if a!={}:raise ValueError("启动或取消只接受空 JSON 对象")
                        self.reply(getattr(state.batch,action)(parts[3]))
                    elif action=="reject" and len(parts)==5:
                        a=strict_json(self.body())
                        if not isinstance(a,dict) or set(a)-{"reason"}:raise ValueError("无效拒绝参数")
                        self.reply(state.batch.reject(parts[3],parts[4],a.get("reason","上传失败")))
                    elif action=="open" and len(parts)==5:
                        a=strict_json(self.body())
                        if a!={}:raise ValueError("打开结果只接受空 JSON 对象")
                        record,result=state.batch.open_result(parts[3],parts[4])
                        with state.lock:
                            state.record=record;state.result=result;state.processed_cache.clear()
                            self.reply({"record":record.summary(),"result":result,"config":result["configuration"]})
                    else:self.reply({"error":"未知批处理接口"},404)
                    return
                with state.lock:
                    if path=="/api/load":
                        raw=self.body(MAX_UPLOAD)
                        if len(raw)<4:raise ValueError("上传内容不完整")
                        n=struct.unpack_from("<I",raw)[0]
                        if not 0<n<=8*1024**2 or n+4>=len(raw):raise ValueError("CFG 长度无效或 DAT 为空")
                        folder=Path(state.temp.name)
                        cfg=folder/"uploaded.cfg";dat=folder/"uploaded.dat"
                        cfg.write_bytes(raw[4:n+4]);dat.write_bytes(raw[n+4:]);del raw
                        record=load(cfg,dat)
                        # Commit a new record only after complete parse; a failed upload
                        # invalidates old results below and never overwrites a valid record.
                        state.record=record;state.result=None;state.processed_cache.clear()
                        self.reply({"record":record.summary()})
                    elif path=="/api/demo":
                        args=json.loads(self.body());variant=args.get("variant","pass")
                        if variant in FIELD_CATALOG:
                            cfg,case,_=generate_field_case(state.temp.name,variant)
                        elif variant in NOISE_CATALOG:
                            cfg,case,_=generate_noise_case(state.temp.name,variant)
                        elif variant in CATALOG:
                            cfg,case,_=generate_case(state.temp.name,variant)
                        elif variant in ("pass","slow","overshoot","alarm","gap","no_trigger","falling","zero_target","wide_pulse","missing"):
                            cfg,case=scenario(state.temp.name,"demo",variant=variant)
                        else:raise ValueError("未知演示场景")
                        state.record=load(cfg);state.result=None;state.processed_cache.clear()
                        self.reply({"record":state.record.summary(),"config":json.loads(case.read_text(encoding="utf-8"))})
                    elif path=="/api/run":
                        if state.record is None:raise ValueError("请先加载录波")
                        config=json.loads(self.body())
                        state.result=None;state.processed_cache.clear()
                        state.result=run(state.record,config)
                        self.reply(state.result)
                    else:self.reply({"error":"未找到"},404)
            except (ValueError,OSError,KeyError,TypeError,OverflowError) as exc:
                if not path.startswith("/api/batch/"):
                    state.result=None;state.processed_cache.clear()
                if path=="/api/load":state.record=None
                self.reply({"error":str(exc)},400)
            except Exception as exc:
                state.result=None;state.processed_cache.clear()
                self.reply({"error":f"内部错误：{type(exc).__name__}；请保留日志供复核"},500)
    return Handler


def serve(port=8765,open_browser=True):
    state=State()
    try:
        server=ThreadingHTTPServer(("127.0.0.1",port),make_handler(state))
    except OSError:
        state.close();raise
    url=f"http://127.0.0.1:{server.server_port}"
    print(f"ESS WaveTest 已启动：{url}\n仅限本机访问；按 Ctrl+C 停止。")
    if open_browser:webbrowser.open(url)
    try:server.serve_forever(poll_interval=.3)
    except KeyboardInterrupt:print("\n正在关闭本地服务。")
    finally:server.server_close();state.close()
