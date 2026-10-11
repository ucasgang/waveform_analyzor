"""Folder planning and bounded, disk-backed batch jobs. Standard library only.
Uploads are stored under generated ids, never under user-supplied paths.
One worker parses one recording at a time; errors do not abort later records.
"""
from __future__ import annotations
import copy, csv, hashlib, html, io, json, re, secrets, shutil, struct, threading, time, zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from .engine import validate, run
from .comtrade import load
from .report import atomic_text, export

MAX_FILES=10000
MAX_ITEMS=2000
MAX_PAIR=128*1024**2
MAX_TOTAL=2*1024**3
TERMINAL={'PASS','FAIL','ERROR','INCONCLUSIVE','CANCELLED'}

def strict_json(raw):
    def reject(v): raise ValueError('JSON 不允许非有限值 '+v)
    def unique(pairs):
        out={}
        for k,v in pairs:
            if k in out: raise ValueError('JSON 重复键 '+k)
            out[k]=v
        return out
    return json.loads(raw.decode('utf-8-sig') if isinstance(raw,bytes) else raw,
                      parse_constant=reject, object_pairs_hook=unique)

def safe_relative(value):
    if not isinstance(value,str) or not value or len(value)>1000:
        raise ValueError('文件相对路径为空或过长')
    if '\\' in value or ':' in value or value.startswith('/') or any(ord(c)<32 for c in value):
        raise ValueError('仅接受无控制字符的相对目录路径')
    parts=value.split('/')
    if any(p in ('','.','..') for p in parts): raise ValueError('拒绝空目录名、. 或 .. 路径')
    return value

def plan_files(files,mode='sibling',recursive=True,browser_limits=True):
    if mode not in ('sibling','common'): raise ValueError('用例模式必须为 sibling 或 common')
    if type(recursive) is not bool: raise ValueError('recursive 必须是布尔值')
    if not isinstance(files,list) or not 1<=len(files)<=MAX_FILES: raise ValueError(f'每批需选择 1..{MAX_FILES} 个文件')
    entries=[]; total=0
    for f in files:
        if not isinstance(f,dict) or set(f)!={'path','size'}:raise ValueError('文件清单只接受 path、size')
        p=safe_relative(f['path']);size=f['size']
        if type(size) is not int or size<0:raise ValueError('文件长度必须为非负整数')
        total+=size;entries.append({'path':p,'size':size})
    if browser_limits and total>MAX_TOTAL:raise ValueError('单批所选文件总量超过 2 GiB；请拆批或使用命令行')
    depths=[e['path'].count('/') for e in entries if Path(e['path']).suffix.lower() in ('.cfg','.dat')]
    # Browser paths include the selected root. For CLI paths, minimum depth is 0.
    # Caller can explicitly remove nested entries before planning for exact root semantics.
    if not recursive and depths:
        depth=min(depths);entries=[e for e in entries if e['path'].count('/')==depth]
    groups={};ignored=0
    for e in entries:
        p=e['path'];ext=Path(p).suffix.lower()
        if ext not in ('.cfg','.dat','.json'):ignored+=1;continue
        key=p[:-len(ext)].casefold();g=groups.setdefault(key,{})
        g.setdefault(ext,[]).append(e)
    rows=[]
    for key,g in sorted(groups.items()):
        if '.cfg' not in g and '.dat' not in g:ignored+=sum(len(v) for v in g.values());continue
        source=next(iter(g.get('.cfg',g.get('.dat'))))['path']
        r={'id':f'r{len(rows)+1:05d}','path':source,'cfg':None,'dat':None,'case':None,'sizes':{},'status':'WAITING','reason':''}
        errors=[]
        for ext,label in (('.cfg','cfg'),('.dat','dat'),('.json','case')):
            if ext=='.json' and mode=='common':continue
            matches=g.get(ext,[])
            if not matches: errors.append('缺少同目录同名 '+ext.upper())
            elif len(matches)>1:errors.append(ext.upper()+' 大小写重名或重复文件；拒绝猜测配对')
            else:r[label]=matches[0]['path'];r['sizes'][label]=matches[0]['size']
        if browser_limits and sum(r['sizes'].values())+8>MAX_PAIR:errors.append('单组超过浏览器 128 MiB 上限')
        if r['sizes'].get('cfg',0)>8*1024**2:errors.append('CFG 超过 8 MiB')
        if r['sizes'].get('case',0)>2*1024**2:errors.append('JSON 用例超过 2 MiB')
        if any(v==0 for v in r['sizes'].values()):errors.append('存在空文件')
        if errors:r.update(status='ERROR',reason='；'.join(errors))
        rows.append(r)
    if not rows:raise ValueError('选择范围内未发现 CFG / DAT 录波；不会把空目录判为通过')
    if len(rows)>MAX_ITEMS:raise ValueError(f'单批最多 {MAX_ITEMS} 组录波，请拆批')
    return {'rows':rows,'selected_files':len(files),'selected_bytes':total,'ignored_files':ignored,'mode':mode}

def csv_summary(snapshot):
    s=io.StringIO();w=csv.writer(s);w.writerow(['序号','相对路径','状态','PASS','FAIL','INCONCLUSIVE','ERROR','用例哈希','CFG哈希','DAT哈希','说明'])
    def safe(v):return "'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v
    for r in snapshot['rows']:
        c=r.get('counts',{});h=r.get('hashes',{})
        w.writerow([safe(v) for v in [r['id'],r['path'],r['status'],*[c.get(x,'') for x in ('PASS','FAIL','INCONCLUSIVE','ERROR')],r.get('config_sha256',''),h.get('cfg_sha256',''),h.get('dat_sha256',''),r['reason']]])
    return '\ufeff'+s.getvalue()

def batch_index(snapshot):
    E=lambda x:html.escape(str(x))
    rows=[]
    for r in snapshot['rows']:
        link=f'<a href="items/{r["id"]}/out/report.html">交互报告</a>' if r.get('has_result') else '无可用录波报告'
        rows.append('<tr>'+''.join(f'<td>{E(r[k])}</td>' for k in ('id','path','status','reason'))+f'<td>{link}</td></tr>')
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>ESS 批量分析汇总</title><style>body{{max-width:1300px;margin:40px auto;padding:20px;font:15px/1.7 "Segoe UI","Microsoft YaHei",sans-serif;color:#18314b;background:#f2f6fa}}h1{{font-size:30px}}table{{width:100%;border-collapse:collapse;background:white}}td,th{{padding:12px;border-bottom:1px solid #dce4ec;text-align:left;overflow-wrap:anywhere}}a{{color:#2167b5}}.card{{padding:24px;background:#142b44;color:white;border-radius:12px}}small{{display:block}}@media(max-width:700px){{table{{font-size:12px}}td,th{{padding:5px}}}}</style><h1>文件夹批量分析汇总</h1><div class="card">批次状态 {E(snapshot['state'])} · 验收汇总 {E(snapshot['verdict'])}<small>{E(snapshot['counts'])}</small></div><p>本页仅汇总实际设备判定，不用预期结果反转验收结论。未执行 / 取消不等于通过。交互报告在解压后离线打开；完整原始文件未重复放入报告包，请保留源文件。</p><table><thead><tr><th>ID</th><th>相对路径</th><th>实际判定</th><th>说明</th><th>查看</th></tr></thead><tbody>{''.join(rows)}</tbody></table></html>'''

class BatchManager:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock();self.jobs={};self.current=None;self.worker=None;self.closed=False
    def create(self,files,mode='sibling',config=None,recursive=True):
        plan=plan_files(files,mode,recursive)
        if mode=='common':validate(config)
        with self.lock:
            if self.closed:raise ValueError('服务正在关闭')
            if any(j['state'] in ('STAGING','RUNNING') for j in self.jobs.values()):raise ValueError('已有批次未结束，请先完成或取消')
            # Keep only the most recent three batches, bound disk growth across jobs.
            while len(self.jobs)>=3:
                old=next(iter(self.jobs));shutil.rmtree(self.root/old,ignore_errors=True);del self.jobs[old]
            retained=sum(j['selected_bytes'] for j in self.jobs.values())
            if retained+plan['selected_bytes']>MAX_TOTAL:raise ValueError('本会话批次源文件预算超过 2 GiB，请导出报告后重启服务')
            ident=secrets.token_hex(12)
            j={'id':ident,'created_utc':datetime.now(timezone.utc).isoformat(),'state':'STAGING','cancel_requested':False,
               **plan,'config':copy.deepcopy(config) if mode=='common' else None}
            self.jobs[ident]=j;self.current=ident;(self.root/ident/'items').mkdir(parents=True)
            return self.snapshot(ident)
    def job(self,ident):
        if ident not in self.jobs:raise ValueError('批次不存在或已清理')
        return self.jobs[ident]
    def row(self,j,item):
        return next((r for r in j['rows'] if r['id']==item),None)
    def upload(self,ident,item,raw):
        with self.lock:
            j=self.job(ident);r=self.row(j,item)
            if j['state']!='STAGING' or r is None or r['status']!='WAITING':raise ValueError('此记录不接受上传（已上传、取消或配对失败）')
            try:
                if len(raw)<8:raise ValueError('上传包头不足 8 字节')
                nc,nj=struct.unpack_from('<II',raw);nd=len(raw)-8-nc-nj
                if (nc,nd,nj)!=(r['sizes']['cfg'],r['sizes']['dat'],r['sizes'].get('case',0)):
                    raise ValueError('上传长度与扫描清单不一致；文件可能已变更或上传中断')
                cfg=raw[8:8+nc];dat=raw[8+nc:8+nc+nd]
                config=strict_json(raw[-nj:]) if nj else copy.deepcopy(j['config'])
                validate(config)
                folder=self.root/ident/'items'/item;folder.mkdir(parents=True,exist_ok=True)
                (folder/'record.cfg').write_bytes(cfg);(folder/'record.dat').write_bytes(dat)
                atomic_text(folder/'case.json',json.dumps(config,ensure_ascii=False,allow_nan=False))
                r.update(status='READY',reason='等待执行',uploaded_hashes={'cfg_sha256':hashlib.sha256(cfg).hexdigest(),'dat_sha256':hashlib.sha256(dat).hexdigest()})
            except (ValueError,OSError,KeyError,TypeError,struct.error) as e:
                r.update(status='ERROR',reason=str(e));raise ValueError(str(e)) from e
            return self.snapshot(ident)
    def reject(self,ident,item,reason):
        with self.lock:
            j=self.job(ident);r=self.row(j,item)
            if j['state']!='STAGING' or r is None or r['status']!='WAITING':raise ValueError('不能覆盖已上传或已结束的记录')
            r.update(status='ERROR',reason=str(reason)[:1000]);return self.snapshot(ident)
    def start(self,ident):
        with self.lock:
            j=self.job(ident)
            if j['state']!='STAGING':raise ValueError('批次已经启动或取消')
            if any(r['status']=='WAITING' for r in j['rows']):raise ValueError('仍有未上传的记录；请完成上传或明确标记上传失败')
            j['state']='RUNNING';self.worker=threading.Thread(target=self._work,args=(ident,),daemon=True);self.worker.start()
            return self.snapshot(ident)
    def _work(self,ident):
        j=self.job(ident)
        for r in j['rows']:
            with self.lock:
                if r['status']!='READY':continue
                if j['cancel_requested'] or self.closed:r.update(status='CANCELLED',reason='用户取消，未执行');continue
                r.update(status='RUNNING',reason='完整解析与判定中')
            folder=self.root/ident/'items'/r['id'];started=time.perf_counter()
            try:
                record=load(folder/'record.cfg',folder/'record.dat')
                if record.hashes!=r['uploaded_hashes']:raise ValueError('源文件哈希发生变化；拒绝执行')
                # Preserve logical source names in the report, not temporary paths.
                record.meta['cfg_file']=r['cfg'];record.meta['dat_file']=r['dat']
                config=strict_json((folder/'case.json').read_bytes());result=run(record,config)
                export(result,folder/'out',record=record)
                with self.lock:r.update(status=result['status'],reason='；'.join(dict.fromkeys(x['reason'] for x in result['results'] if x['status']!='PASS')) or '全部断言通过',
                                        counts=result['counts'],config_sha256=result['config_sha256'],hashes=result['record']['hashes'],has_result=True)
                del record,result
            except Exception as exc:
                with self.lock:r.update(status='ERROR',reason=f'{type(exc).__name__}: {exc}'[:2000],has_result=False)
                try:atomic_text(folder/'out/error.json',json.dumps({'status':'ERROR','source':r['path'],'reason':r['reason']},ensure_ascii=False))
                except OSError:pass
            with self.lock:r['elapsed_s']=round(time.perf_counter()-started,6)
        with self.lock:
            j['state']='CANCELLED' if j['cancel_requested'] else 'COMPLETE';j['finished_utc']=datetime.now(timezone.utc).isoformat()
        try:self.write_summary(ident)
        except OSError:
            with self.lock:j['export_warning']='汇总写入失败；请检查可用磁盘空间'
    def cancel(self,ident):
        with self.lock:
            j=self.job(ident)
            if j['state'] not in ('STAGING','RUNNING'):return self.snapshot(ident)
            j['cancel_requested']=True
            for r in j['rows']:
                if r['status'] in ('WAITING','READY'):r.update(status='CANCELLED',reason='用户取消，未执行')
            if j['state']=='STAGING':j['state']='CANCELLED'
            return self.snapshot(ident)
    def snapshot(self,ident=None):
        with self.lock:
            if ident is None:ident=self.current
            if ident is None:return {'current':None}
            j=self.job(ident);v=copy.deepcopy({k:x for k,x in j.items() if k!='config'})
            counts={s:sum(r['status']==s for r in j['rows']) for s in ('PASS','FAIL','INCONCLUSIVE','ERROR','CANCELLED','WAITING','READY','RUNNING')}
            v['counts']=counts;v['total']=len(j['rows']);v['done']=sum(counts[s] for s in TERMINAL)
            v['verdict']=next((s for s in ('ERROR','FAIL','INCONCLUSIVE') if counts[s]),'PASS')
            if v['verdict']=='PASS' and (v['done']<v['total'] or counts['CANCELLED']):v['verdict']='INCONCLUSIVE'
            return v
    def write_summary(self,ident):
        snapshot=self.snapshot(ident);folder=self.root/ident
        atomic_text(folder/'summary.json',json.dumps(snapshot,ensure_ascii=False,indent=2))
        atomic_text(folder/'summary.csv',csv_summary(snapshot));atomic_text(folder/'index.html',batch_index(snapshot))
    def export_zip(self,ident):
        with self.lock:
            j=self.job(ident)
            if j['state'] not in ('COMPLETE','CANCELLED'):raise ValueError('批次尚未结束，请完成或取消后导出')
            folder=self.root/ident;target=folder/'reports.zip'
            if target.exists():return target
            self.write_summary(ident)
            temporary=folder/'reports.zip.tmp'
            try:
                with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
                    for name in ('summary.json','summary.csv','index.html'):z.write(folder/name,name)
                    for p in sorted((folder/'items').glob('*/out/*')):
                        if p.is_file():z.write(p,p.relative_to(folder).as_posix())
                temporary.replace(target)
            finally:
                if temporary.exists():temporary.unlink()
            return target

    def open_result(self,ident,item):
        with self.lock:
            j=self.job(ident);r=self.row(j,item)
            if r is None or not r.get('has_result'):raise ValueError('此记录没有已完成的结果')
            folder=self.root/ident/'items'/item
            result=strict_json((folder/'out/result.json').read_bytes())
            record=load(folder/'record.cfg',folder/'record.dat')
            if record.hashes!=result['record']['hashes']:raise ValueError('录波与结果哈希不匹配')
            record.meta['cfg_file']=r['cfg'];record.meta['dat_file']=r['dat']
            return record,result
    def close(self):
        with self.lock:
            self.closed=True
            for j in self.jobs.values():j['cancel_requested']=True
        if self.worker and self.worker.is_alive():self.worker.join()
