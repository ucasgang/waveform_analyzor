"""Self-contained, escaped HTML; machine-readable JSON, CSV and JUnit exports."""
from __future__ import annotations
import csv
import html
import io
import json
import math
import os
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

LABEL={"PASS":"通过","FAIL":"失败","INCONCLUSIVE":"数据不足 / 待复核","ERROR":"配置或执行错误"}
COLORS={"PASS":"#087f68","FAIL":"#c23e48","INCONCLUSIVE":"#aa6b00","ERROR":"#7544ba"}
NAMES={"relation_max_error":"跨通道最大残差","relation_violation_s":"跨通道超容差时长 (s)","raw_minimum":"原始最小值（硬限值）","raw_maximum":"原始最大值（硬限值）","repair_fraction":"修复时间占比","raw_peak_review":"原始峰值冲突复核","baseline_noise_sigma":"基线 MAD 尺度","steady_noise_sigma":"稳态 MAD 尺度","baseline_drift":"基线漂移","steady_drift":"稳态漂移","rejected_edges":"被抑制边沿数","longest_chatter_s":"最长抖动簇 (s)","clip_fraction":"量程边界占比","preprocessing_resolution":"滤波窗口欠采样数","overshoot_pct":"超调量 (%)","response_s":"响应时间 (s)","rise_s":"上升/下降时间 (s)",
       "latency_s":"低阈值延时 (s)","settling_s":"调节时间 (s)","steady_error_pct":"稳态误差 (%)",
       "steady_ripple_pp":"稳态峰峰值","edge_count":"变位次数","pulse_count":"脉冲数",
       "violating_samples":"不符样本数","rms":"有效值","minimum":"最小值","maximum":"最大值"}


def esc(v): return html.escape(str(v),quote=True)

def fmt(v):
    if v is None: return "未取得"
    if isinstance(v,(int,float)): return f"{v:.7g}"
    return str(v)


def limit_text(c):
    lo,hi=c.get("min"),c.get("max")
    if lo is not None and hi is not None:
        return f"= {fmt(lo)}" if lo==hi else f"{fmt(lo)} 至 {fmt(hi)}"
    if hi is not None:return f"≤ {fmt(hi)}"
    if lo is not None:return f"≥ {fmt(lo)}"
    return "仅观测"


def svg_trace(item):
    e=item.get("evidence",{}); data=e.get("raw_trace",[])
    if len(data)<2:return ""
    w,h=1050,230;left,right,top,bottom=72,20,24,38
    xmin,xmax=data[0][0],data[-1][0];ys=[p[1] for p in data+e.get("processed_trace",[]) if p[1] is not None]
    if e.get("trace_kind")=="digital": ymin,ymax=-.15,1.15
    else:
        ymin,ymax=min(ys),max(ys)
        if "target" in e:ymin=min(ymin,e["target"]-e["band_absolute"]);ymax=max(ymax,e["target"]+e["band_absolute"])
        margin=(ymax-ymin)*.12 or 1.;ymin-=margin;ymax+=margin
    x=lambda v:left+(v-xmin)/(xmax-xmin or 1)*(w-left-right)
    y=lambda v:top+(ymax-v)/(ymax-ymin or 1)*(h-top-bottom)
    parts=[f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{esc(item["name"])} 证据波形" xmlns="http://www.w3.org/2000/svg"><rect width="100%" height="100%" fill="#f7fafd" rx="10"/>']
    for j in range(5):
        yy=top+j*(h-top-bottom)/4;v=ymax-j*(ymax-ymin)/4
        parts.append(f'<path d="M{left} {yy}H{w-right}" stroke="#dde5ed"/><text x="{left-9}" y="{yy+4}" text-anchor="end" font-size="11" fill="#617388">{v:.5g}</text>')
    for j in range(6):
        xx=left+j*(w-left-right)/5;v=xmin+j*(xmax-xmin)/5
        parts.append(f'<text x="{xx}" y="{h-13}" text-anchor="middle" font-size="11" fill="#617388">{v:.5g}s</text>')
    if "target" in e:
        target=e["target"];band=e["band_absolute"]
        parts.append(f'<rect x="{left}" y="{y(target+band)}" width="{w-left-right}" height="{y(target-band)-y(target+band)}" fill="#d9f1e9"/><path d="M{left} {y(target)}H{w-right}" stroke="#0e9b7e" stroke-dasharray="5 4"/>')
    path=[]
    for i,(tx,vy) in enumerate(data):
        if i==0:path.append(f"M{x(tx):.2f},{y(vy):.2f}")
        elif e.get("trace_kind")=="digital":path.append(f"H{x(tx):.2f}V{y(vy):.2f}")
        else:path.append(f"L{x(tx):.2f},{y(vy):.2f}")
    parts.append(f'<path d="{" ".join(path)}" fill="none" stroke="#2367cc" stroke-width="1.6"/>')
    overlay=e.get("processed_trace",[])
    if overlay:
        path=[];pen=False
        for tx,vy in overlay:
            if vy is None:pen=False;continue
            path.append((f"H{x(tx):.2f}V{y(vy):.2f}" if e.get("trace_kind")=="digital" else f"L{x(tx):.2f},{y(vy):.2f}") if pen else f"M{x(tx):.2f},{y(vy):.2f}")
            pen=True
        parts.append(f'<path class="processed" d="{" ".join(path)}" fill="none" stroke="#ca6b16" stroke-width="1.8"/>')
        for p in e.get("processing",{}).get("analysis",{}).get("repairs",[])[:256]:
            parts.append(f'<circle cx="{x(p["time_s"]):.2f}" cy="{y(p["raw"]):.2f}" r="3" fill="none" stroke="#bb4763"/>')
    tr=e.get("trigger",{}).get("at")
    if tr is not None and xmin<=tr<=xmax:
        parts.append(f'<path d="M{x(tr)} {top}V{h-bottom}" stroke="#c08632" stroke-dasharray="4 4"/>')
    parts.append(f'<text x="{left}" y="15" font-size="11" fill="#617388">{esc(e.get("unit","逻辑值"))} · 录波起点相对时间；蓝线为原始测量；橙线为显式处理分支（如有）；圆点为候选修复</text></svg>')
    return "".join(parts)


def noise_panel(item):
    e=item["evidence"];p=e.get("processing");d=e.get("digital_noise")
    if not p and not d:return ""
    if d:
        return '<div class="notice"><b>数字量去抖审计</b><p>'+esc(f"原始边沿 {d['raw_edges']} / 确认边沿 {d['qualified_edges']} / 抑制边沿 {d['rejected_edges']}；最长抖动 {d['longest_chatter_s']*1000:.5g} ms")+'</p><p>数字去抖只执行显式配置，不能证明被抑制脉冲不是保护动作。</p></div>'
    rows=[]
    for key in ("overshoot_pct","response_s","rise_s","settling_s","steady_error_pct","steady_ripple_pp"):
        rows.append('<tr><td>'+esc(NAMES[key])+'</td><td>'+esc(fmt(e['raw_metrics'].get(key)))+'</td><td>'+esc(fmt(item['metrics'].get(key)))+'</td></tr>')
    text=f"候选替换 {p['analysis']['replaced_samples']} 个采样；时间权重占比 {p['analysis']['replaced_fraction']*100:.5g}%。原始硬限值永不滤波，峰值冲突需要复核。"
    return '<div class="notice"><b>抗噪审计：原始与处理双分支</b><p>'+esc(text)+'</p><p>稳态误差在抗噪分支使用时间加权中位数，响应使用持续确认，调节时间使用固定误差带与占用率；两分支定义并不完全相同。</p></div><table><thead><tr><th>指标</th><th>原始分支</th><th>抗噪分支</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table>'


def _html_report(result):
    cards=[]
    for s in LABEL:
        cards.append(f'<div class="stat"><span>{esc(LABEL[s])}</span><strong style="color:{COLORS[s]}">{result["counts"][s]}</strong></div>')
    blocks=[]
    for item in result["results"]:
        rows=[]
        for c in item["checks"]:
            rows.append(f'<tr><td>{esc(NAMES.get(c["metric"],c["metric"]))}</td><td class="mono">{esc(fmt(c["value"]))}</td><td>{esc(limit_text(c))}</td><td><b style="color:{COLORS[c["status"]]}">{esc(LABEL[c["status"]])}</b></td><td>{esc(c["reason"])}</td></tr>')
        st=item["standard"]
        std=f'{st.get("reference","未指定标准")} / {st.get("clause","未指定条款")} / '+("用户声明已审核" if st.get("reviewed") else "未审核或演示阈值")
        e=item["evidence"];mapping=e.get("mapping",{})
        blocks.append(f'''<section class="test"><div class="testhead"><div><small>{esc(item['id'])} · {esc(item['type'])} · {esc(item['channel'])}</small><h2>{esc(item['name'])}</h2></div><span class="badge" style="color:{COLORS[item['status']]}">{esc(LABEL[item['status']])}</span></div>
        <p>{esc(item['reason'])}</p><p class="meta">依据：{esc(std)}</p>
        {noise_panel(item)}
        {svg_trace(item)}
        {('<table><thead><tr><th>指标</th><th>测量结果</th><th>配置限值</th><th>判定</th><th>说明</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table>') if rows else ''}
        <p class="meta">通道映射：{esc(mapping)}<br>最大局部采样间隔：{esc(fmt(e.get('max_sample_interval_s')))} s。图形可抽稀显示；断言使用完整采样及显式处理分支，不使用显示抽稀数据。时间指标不是高于采样精度的硬件测量保证。</p>
        <details><summary>查看完整测量指标与证据元数据</summary><pre>{esc(json.dumps({'metrics':item['metrics'],'evidence':{k:v for k,v in e.items() if k not in ('raw_trace','processed_trace')}},ensure_ascii=False,indent=2))}</pre></details></section>''')
    rec=result["record"]
    warn=''.join(f'<p>• {esc(w)}</p>' for w in rec["warnings"]) or '<p>解析器未发现已覆盖的数据质量异常；这不等同于现场采集质量认证。</p>'
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(result['suite'])} - ESS WaveTest</title>
    <style>
    :root{{--ink:#172b45;--muted:#66788c;--line:#dce5ed}}*{{box-sizing:border-box}}body{{margin:0;background:#edf2f6;color:var(--ink);font:15px/1.65 "Segoe UI","Microsoft YaHei",sans-serif}}.wrap{{max-width:1160px;margin:auto;padding:36px 26px}}header{{background:#142a43;color:#fff;padding:40px;border-radius:18px}}.brand{{font-size:12px;letter-spacing:3px;color:#79d3c1}}h1{{font-size:30px;line-height:1.4;margin:18px 0}}h2{{font-size:20px;margin:5px 0}}header p{{color:#bed0e2}}.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:22px 0}}.stat,.test,.notice{{background:white;border:1px solid var(--line);border-radius:13px;padding:23px}}.stat span{{font-size:13px;color:var(--muted)}}.stat strong{{display:block;font-size:34px;line-height:1.5}}.test{{margin:20px 0;break-inside:avoid}}.testhead{{display:flex;justify-content:space-between;align-items:center;gap:16px}}.badge{{font-size:14px;font-weight:700;border:1px solid;padding:5px 12px;border-radius:24px}}small,.meta{{font-size:12px;color:var(--muted)}}.notice{{background:#fff9ee;border-color:#e6d7b9}}table{{border-collapse:collapse;width:100%;margin-top:16px;font-size:13px}}th,td{{padding:11px 9px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}}th{{background:#f3f7fa}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f7fa;padding:18px;font:12px/1.6 Consolas,monospace}}.mono{{font-family:Consolas,monospace}}details{{margin-top:12px}}summary{{cursor:pointer;color:#2367cc}}footer{{font-size:12px;color:var(--muted);padding:25px 0;overflow-wrap:anywhere}}.actions{{text-align:right;margin:0 0 15px}}button{{border:0;border-radius:7px;padding:10px 18px;color:white;background:#2367cc;cursor:pointer}}svg{{display:block;width:100%;margin-top:18px}}@media(max-width:650px){{.stats{{grid-template-columns:repeat(2,1fr)}}.wrap{{padding:12px}}header{{padding:23px}}.test{{padding:15px}}h1{{font-size:23px}}table{{font-size:11px}}}}@media print{{body{{background:white}}.wrap{{padding:0;max-width:none}}.actions,details{{display:none}}header{{border-radius:0;print-color-adjust:exact}}.test{{break-inside:auto}}table,svg{{break-inside:avoid}}}}
    </style></head><body><main class="wrap"><div class="actions"><button onclick="window.print()">打印 / 保存 PDF</button></div><header><div class="brand">ESS WAVETEST / FACTORY ACCEPTANCE</div><h1>{esc(result['suite'])}</h1><p>总体结果：{esc(LABEL[result['status']])} · {esc(result['generated_utc'])}</p><p>{esc(rec['cfg_file'])} / {esc(rec['format'])} · {rec['samples']:,} 点 · {rec['analog_count']} 模拟通道 + {rec['digital_count']} 数字通道</p></header><div class="stats">{''.join(cards)}</div>
    <div class="notice"><b>验收边界</b><p>{esc(result['notice'])}</p><p>所有时间以本文件录波起点为参考；模拟 skew 已按 CFG 单独应用。没有自动对齐多文件时钟、自动修复缺点或默认标准限值。</p></div>
    {''.join(blocks)}<section class="test"><h2>数据质量与可追溯性</h2>{warn}<pre>{esc(json.dumps({'software_version':result['version'],'hashes':rec['hashes'],'config_sha256':result['config_sha256'],'time_source':rec['time_source'],'time_metadata':rec['time_metadata']},ensure_ascii=False,indent=2))}</pre><details><summary>展开本次完整配置快照</summary><pre>{esc(json.dumps(result['configuration'],ensure_ascii=False,indent=2))}</pre></details></section><footer>ESS WaveTest {esc(result['version'])} · 离线分析 · SHA-256 用于文件一致性核对，不是数字签名。请结合原始录波、测量系统校准和适用标准审核结论。</footer></main></body></html>'''


def html_report(result,record=None):
    from .interactive_report import enhance
    return enhance(_html_report(result),result,record)


def csv_text(result):
    out=io.StringIO(); w=csv.writer(out)
    w.writerow(["用例ID","名称","类型","通道","总体判定","指标","测量值","最小限值","最大限值","指标判定","说明"])
    def safe(v):
        # Protect spreadsheet consumers from formula injection in untrusted names.
        if isinstance(v,str) and v.lstrip().startswith(("=","+","-","@")):return "'"+v
        return v
    for i in result["results"]:
        cs=i["checks"] or [{"metric":"","value":"","min":"","max":"","status":i["status"],"reason":i["reason"]}]
        for c in cs:w.writerow([safe(v) for v in [i["id"],i["name"],i["type"],i["channel"],i["status"],c["metric"],c["value"],c.get("min"),c.get("max"),c["status"],c["reason"]]])
    return "\ufeff"+out.getvalue()


def junit_text(result):
    suite=ET.Element("testsuite",name=result["suite"],tests=str(len(result["results"])),failures=str(result["counts"]["FAIL"]),errors=str(result["counts"]["ERROR"]+result["counts"]["INCONCLUSIVE"]))
    props=ET.SubElement(suite,"properties")
    ET.SubElement(props,"property",name="config_sha256",value=result["config_sha256"])
    for i in result["results"]:
        c=ET.SubElement(suite,"testcase",name=i["id"]+" "+i["name"],classname=i["type"])
        if i["status"]!="PASS": ET.SubElement(c,"failure" if i["status"]=="FAIL" else "error",type=i["status"],message=i["reason"]).text=json.dumps(i["metrics"],ensure_ascii=False)
    return '<?xml version="1.0" encoding="utf-8"?>\n'+ET.tostring(suite,encoding="unicode")


def atomic_text(path:Path,text:str):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    name=None
    try:
        with tempfile.NamedTemporaryFile("w",encoding="utf-8",dir=path.parent,delete=False) as f:
            name=f.name;f.write(text);f.flush();os.fsync(f.fileno())
        os.replace(name,path);name=None
    finally:
        if name and os.path.exists(name):os.unlink(name)


def export(result,folder:str|Path,record=None):
    folder=Path(folder)
    files={"report.html":html_report(result,record=record),"result.json":json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),
           "metrics.csv":csv_text(result),"junit.xml":junit_text(result)}
    for name,text in files.items(): atomic_text(folder/name,text)
    return [str(folder/name) for name in files]
