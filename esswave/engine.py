"""Deterministic assertions with explicit evidence and conservative data quality gates."""
from __future__ import annotations
from array import array
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import math
from statistics import median
from typing import Any
from . import __version__
from .comtrade import Record, Channel
from .robust import (validate_robust,validate_noise,analyze_robust_step,digital_qualify,
                     digital_audit,trigger_crossings)

class ConfigError(ValueError): pass
class InsufficientData(ValueError): pass

TOP_FIELDS={"schema_version","name","description","channels","tests","standard"}
MAP_FIELDS={"kind","name","names","index","pattern","unit","output_unit","scale","offset","side"}
COMMON={"id","name","type","channel","trigger","window","description","standard","max_gap_s"}
FIELDS={
 "digital_edge":{"noise","edge","count","debounce_s","latency_min_s","latency_max_s","guard_band_s"},
 "digital_pulse":{"noise","active","count","min_width_s","max_width_s","debounce_s","guard_band_s"},
 "digital_hold":{"noise","value","debounce_s"},
 "analog_relation":{"references","offset","tolerance","max_violation_s","hard_max_error","alignment_error_bound"},
 "analog_range":{"min","max"},
 "analog_rms":{"min","max"},
 "analog_step":{"robust","target","target_channel","target_window","target_tolerance","baseline_window",
                "steady_last_s","normalization","rated","settle_band_pct","settle_hold_s",
                "low_fraction","high_fraction","limits","guard_band_s"}}
LIMITS={"overshoot_pct_max","response_s_max","rise_s_max","latency_s_max","settling_s_max",
        "steady_error_pct_max","steady_ripple_pp_max"}
TRIGGER_FIELDS={"type","at","channel","edge","threshold","occurrence","debounce_s","search","noise","hysteresis","hold_s"}


def number(x: Any, label: str, minimum=None) -> float:
    if isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x):
        raise ConfigError(f"{label} 必须是有限数值（不能是字符串或布尔值）")
    if minimum is not None and x<minimum: raise ConfigError(f"{label} 不能小于 {minimum}")
    return float(x)


def integer(x, label, minimum=0):
    number(x,label,minimum)
    if int(x)!=x: raise ConfigError(f"{label} 必须为整数")
    return int(x)


def fields(obj,allowed,label):
    if not isinstance(obj,dict): raise ConfigError(f"{label} 必须是对象")
    unknown=set(obj)-allowed
    if unknown: raise ConfigError(f"{label} 含未知字段: {', '.join(sorted(unknown))}")


def win(value, label):
    if not isinstance(value,list) or len(value)!=2: raise ConfigError(f"{label} 必须为 [开始秒,结束秒]")
    a,b=number(value[0],label),number(value[1],label)
    if a>=b: raise ConfigError(f"{label} 的结束时间必须大于开始时间")
    return a,b


def standard_check(s):
    fields(s,{"reference","clause","reviewed","reviewer","note"},"standard")
    if "reviewed" in s and not isinstance(s["reviewed"],bool): raise ConfigError("reviewed 必须为布尔值")
    for k in ("reference","clause","reviewer","note"):
        if k in s and not isinstance(s[k],str): raise ConfigError(f"standard.{k} 必须为字符串")
    if s.get("reviewed") and not all(s.get(k,"").strip() for k in ("reference","clause","reviewer")):
        raise ConfigError("已审核标准必须填写 reference、clause 和 reviewer；此声明不等同于软件认证")


def validate(config: dict) -> None:
    fields(config,TOP_FIELDS,"用例文件")
    if type(config.get("schema_version")) is not int or config.get("schema_version")!=1:
        raise ConfigError("schema_version 必须为整数 1")
    if not isinstance(config.get("name"),str) or not config["name"].strip(): raise ConfigError("用例 name 不能为空")
    if not isinstance(config.get("channels"),dict) or not config["channels"]: raise ConfigError("channels 不能为空")
    if len(config["channels"])>4096: raise ConfigError("通道别名超过 4096")
    for alias,m in config["channels"].items():
        if not isinstance(alias,str) or not alias: raise ConfigError("通道别名不能为空")
        fields(m,MAP_FIELDS,f"通道 {alias}")
        if m.get("kind") not in ("analog","digital"): raise ConfigError(f"{alias}.kind 必须为 analog 或 digital")
        if sum(k in m for k in ("name","names","index","pattern"))!=1:
            raise ConfigError(f"{alias} 需且仅需一个 name/names/index/pattern 选择器")
        for k in ("name","pattern","unit","output_unit"):
            if k in m and (not isinstance(m[k],str) or not m[k]): raise ConfigError(f"{alias}.{k} 必须为非空字符串")
        if "names" in m and (not isinstance(m["names"],list) or not m["names"] or not all(isinstance(s,str) and s for s in m["names"])):
            raise ConfigError(f"{alias}.names 必须为非空字符串数组")
        if "index" in m: integer(m["index"],f"{alias}.index",1)
        for k in ("scale","offset"):
            if k in m: number(m[k],f"{alias}.{k}")
        if m.get("scale",1)==0: raise ConfigError("通道 scale 不能为 0")
        if m.get("side","native") not in ("native","primary","secondary"): raise ConfigError("side 必须为 native/primary/secondary")
        if m["kind"]=="digital" and any(k in m for k in ("scale","offset","side","unit","output_unit")):
            raise ConfigError("数字通道不支持模拟缩放、单位或变比；有效电平请在断言中指定")
    tests=config.get("tests")
    if not isinstance(tests,list) or not 1<=len(tests)<=1000: raise ConfigError("tests 需包含 1..1000 个断言")
    ids=set()
    if "standard" in config: standard_check(config["standard"])
    for a in tests:
        if not isinstance(a,dict): raise ConfigError("每个断言必须为对象")
        typ=a.get("type")
        if typ not in FIELDS: raise ConfigError(f"未知断言类型 {typ}")
        fields(a,COMMON|FIELDS[typ],f"断言 {a.get('id','?')}")
        if not isinstance(a.get("id"),str) or not a["id"] or a["id"] in ids: raise ConfigError("断言 id 必须非空且唯一")
        ids.add(a["id"])
        if a.get("channel") not in config["channels"]: raise ConfigError(f"{a['id']} 引用未定义通道")
        kind="digital" if typ.startswith("digital") else "analog"
        if config["channels"][a["channel"]]["kind"]!=kind: raise ConfigError(f"{a['id']} 断言与通道类型不匹配")
        win(a.get("window"),f"{a['id']}.window")
        if "standard" in a: standard_check(a["standard"])
        for k in ("debounce_s","guard_band_s","latency_min_s","latency_max_s","min_width_s","max_width_s","max_gap_s"):
            if k in a: number(a[k],k,0)
        for lo,hi in (("latency_min_s","latency_max_s"),("min_width_s","max_width_s")):
            if lo in a and hi in a and a[lo]>a[hi]: raise ConfigError(f"{lo} 不能大于 {hi}")
        tr=a.get("trigger",{"type":"time","at":0})
        fields(tr,TRIGGER_FIELDS,"trigger")
        tt=tr.get("type")
        if tt=="time":
            if set(tr)-{"type","at"}: raise ConfigError("time 触发器只能包含 type 和 at")
            number(tr.get("at"),"trigger.at")
        elif tt in ("digital_edge","analog_crossing"):
            allowed={"type","channel","edge","occurrence","search"}|({"debounce_s","noise"} if tt=="digital_edge" else {"threshold","hysteresis","hold_s"})
            if set(tr)-allowed: raise ConfigError("触发器包含不适用于该类型的字段")
            if tr.get("channel") not in config["channels"]: raise ConfigError("触发器通道未定义")
            expected="digital" if tt=="digital_edge" else "analog"
            if config["channels"][tr["channel"]]["kind"]!=expected: raise ConfigError("触发器通道类型不匹配")
            if tr.get("edge","rising") not in ("rising","falling"): raise ConfigError("触发器 edge 必须为 rising/falling")
            integer(tr.get("occurrence",1),"trigger.occurrence",1)
            if "search" in tr: win(tr["search"],"trigger.search")
            if tt=="analog_crossing":
                number(tr.get("threshold"),"trigger.threshold")
                number(tr.get("hysteresis",0),"trigger.hysteresis",0)
                number(tr.get("hold_s",0),"trigger.hold_s",0)
            else:
                number(tr.get("debounce_s",0),"trigger.debounce_s",0)
                if "noise" in tr:
                    validate_noise(tr["noise"])
                    if "debounce_s" in tr:raise ConfigError("trigger.noise 与 debounce_s 不能同时配置")
        else: raise ConfigError("未知触发器类型")
        if "noise" in a:
            validate_noise(a["noise"])
            if "debounce_s" in a:raise ConfigError("noise 与 debounce_s 不能同时配置")
            if typ=="digital_pulse" and "min_width_s" in a:
                active_hold=a["noise"]["rise_s" if a.get("active",1) else "fall_s"]
                if active_hold>a["min_width_s"]+1e-12:
                    raise ConfigError("有效电平去抖时间大于最小合法脉宽，会抹掉合法保护脉冲；请缩短去抖或使用原始分支")
        if "robust" in a:validate_robust(a["robust"])
        if typ=="digital_edge":
            if a.get("edge","rising") not in ("rising","falling","any"): raise ConfigError("edge 必须为 rising/falling/any")
            integer(a.get("count",1),"count")
        elif typ=="digital_pulse":
            if a.get("active",1) not in (0,1) or isinstance(a.get("active",1),bool): raise ConfigError("active 必须为 0 或 1")
            integer(a.get("count",1),"count")
            if not {"min_width_s","max_width_s"}&set(a): raise ConfigError("pulse 至少需要一个脉宽限值")
        elif typ=="digital_hold":
            if a.get("value") not in (0,1) or isinstance(a.get("value"),bool): raise ConfigError("hold.value 必须为 0 或 1")
        elif typ=="analog_relation":
            from .relation import validate_relation
            validate_relation(a,config)
        elif typ in ("analog_range","analog_rms"):
            if "min" not in a and "max" not in a: raise ConfigError("至少需要 min 或 max")
            for k in ("min","max"):
                if k in a: number(a[k],k)
            if a.get("min",-math.inf)>a.get("max",math.inf): raise ConfigError("min 不能大于 max")
        else:
            if sum(k in a for k in ("target","target_channel"))!=1: raise ConfigError("step 需且仅需 target 或 target_channel")
            if "target" in a: number(a["target"],"target")
            if "target_channel" in a:
                if a["target_channel"] not in config["channels"] or config["channels"][a["target_channel"]]["kind"]!="analog": raise ConfigError("target_channel 必须指向模拟量别名")
                win(a.get("target_window"),"target_window")
            if a["window"][0]!=0: raise ConfigError("analog_step.window 必须从触发相对时间 0 开始")
            bw=win(a.get("baseline_window"),"baseline_window")
            if bw[1]>0: raise ConfigError("baseline_window 必须位于触发之前")
            for k in ("target_tolerance","steady_last_s","rated","settle_band_pct","settle_hold_s"):
                if k in a: number(a[k],k,0)
            if a.get("steady_last_s",.2)<=0 or a.get("settle_hold_s",.1)<=0: raise ConfigError("稳态窗口和最小保持时间必须大于 0")
            if a.get("steady_last_s",.2)>a["window"][1]: raise ConfigError("稳态窗口长度大于分析窗口")
            norm=a.get("normalization","step")
            if norm not in ("step","rated","target"): raise ConfigError("normalization 必须为 step/rated/target")
            if norm=="rated" and number(a.get("rated"),"rated",0)<=0: raise ConfigError("rated 必须大于 0")
            low=number(a.get("low_fraction",.1),"low_fraction")
            high=number(a.get("high_fraction",.9),"high_fraction")
            if not 0<low<high<=1: raise ConfigError("要求 0 < low_fraction < high_fraction <= 1")
            fields(a.get("limits"),LIMITS,"limits")
            if not a["limits"]: raise ConfigError("step.limits 不能为空")
            for k,v in a["limits"].items(): number(v,k,0)

@dataclass
class Signal:
    channel: Channel
    time: array
    values: array
    unit: str
    mapping: dict


def resolve(record: Record, alias: str, config: dict) -> Signal:
    m=config["channels"][alias]
    candidates=[c for c in record.channels if c.kind==m["kind"]]
    if "index" in m: candidates=[c for c in candidates if c.index==m["index"]]
    elif "name" in m: candidates=[c for c in candidates if c.name==m["name"]]
    elif "names" in m: candidates=[c for c in candidates if c.name in m["names"]]
    else: candidates=[c for c in candidates if fnmatch.fnmatchcase(c.name,m["pattern"])]
    if not candidates: raise ConfigError(f"通道 {alias} 未匹配到录波通道")
    if len(candidates)>1: raise ConfigError(f"通道 {alias} 匹配到 {len(candidates)} 个通道；请用 index 消除歧义")
    c=candidates[0]
    if "unit" in m and c.unit.strip()!=m["unit"].strip():
        raise ConfigError(f"通道 {alias} 单位不匹配：CFG={c.unit!r}，期望={m['unit']!r}；禁止自动猜测换算")
    t=record.time if c.skew_us==0 else array("d",(v+c.skew_us*1e-6 for v in record.time))
    factor=1.; side=m.get("side","native")
    if record.meta["revision"]==1991 and side!="native":
        raise ConfigError("1991 记录未提供变比与一次/二次侧信息；只能使用 native")
    output_unit=m.get("output_unit",c.unit)
    if output_unit!=c.unit and "scale" not in m:
        raise ConfigError("变更 output_unit 必须同时显式提供 scale；禁止只改单位标签")
    if side=="primary" and c.side=="S": factor=c.primary/c.secondary
    if side=="secondary" and c.side=="P": factor=c.secondary/c.primary
    factor*=m.get("scale",1.); offset=m.get("offset",0.)
    if not math.isfinite(factor): raise ConfigError("通道变比与缩放相乘溢出；请检查单位与配置")
    y=record.values[c.key]
    if factor!=1 or offset!=0:
        original=y;y=array("d",(v*factor+offset for v in original))
        if any(math.isfinite(v) and not math.isfinite(w) for v,w in zip(original,y)):
            raise ConfigError("通道换算后工程值溢出；禁止当作传感器缺失值处理")
    if c.kind=="analog" and not c.unit.strip():
        raise ConfigError(f"通道 {alias} 的 CFG 单位为空；请在源 CFG 明确单位后分析")
    return Signal(c,t,y,output_unit,{"alias":alias,"key":c.key,"name":c.name,"native_unit":c.unit,"output_unit":output_unit,
                  "side":side,"scale":m.get("scale",1),"offset":offset,"factor_including_ratio":factor,"skew_us":c.skew_us})


def coverage(record, sig, start, end, max_gap=None):
    if start<sig.time[0]-1e-9 or end>sig.time[-1]+1e-9:
        raise InsufficientData(f"所需窗口 [{start:.6g}, {end:.6g}] s 超出有效录波 [{sig.time[0]:.6g}, {sig.time[-1]:.6g}] s")
    lo=max(0,bisect_right(sig.time,start)-1); hi=min(len(sig.time),bisect_left(sig.time,end)+1)
    if hi-lo<2: raise InsufficientData("窗口内不足两个采样点")
    if any(not math.isfinite(v) for v in sig.values[lo:hi]): raise InsufficientData("分析窗口包含缺失或非有限采样值")
    shift=sig.channel.skew_us*1e-6
    if any(l+shift<end and r+shift>start for l,r in record.gaps): raise InsufficientData("分析窗口跨越疑似采样间隙")
    if max_gap is not None and any(sig.time[i]-sig.time[i-1]>max_gap+1e-12 for i in range(lo+1,hi)):
        raise InsufficientData("分析窗口的采样间隔超过 max_gap_s")
    return lo,hi


def interpolate(t,y,x):
    i=bisect_left(t,x)
    if i==0: return float(y[0])
    if i==len(t): return float(y[-1])
    if abs(t[i]-x)<1e-12: return float(y[i])
    return y[i-1]+(y[i]-y[i-1])*(x-t[i-1])/(t[i]-t[i-1])


def segment(record,sig,start,end,max_gap=None):
    coverage(record,sig,start,end,max_gap)
    lo,hi=bisect_right(sig.time,start),bisect_left(sig.time,end)
    t=[start]+list(sig.time[lo:hi])+[end]
    y=[interpolate(sig.time,sig.values,start)]+list(sig.values[lo:hi])+[interpolate(sig.time,sig.values,end)]
    return t,y


def mean_time(t,y):
    return math.fsum((t[i]-t[i-1])*(y[i]+y[i-1])*.5 for i in range(1,len(t)))/(t[-1]-t[0])


def rms_time(t,y):
    # Exact integral of square of a piecewise-linear reconstructed signal.
    total=math.fsum((t[i]-t[i-1])*(y[i-1]**2+y[i-1]*y[i]+y[i]**2)/3 for i in range(1,len(t)))
    return math.sqrt(max(0.,total/(t[-1]-t[0])))


def debounced(t,y,hold,until=None):
    """Qualified transitions use raw onset times, confirmed by observed hold.
    Initial level is observed, not inferred. Unconfirmed trailing runs are retained
    as pending evidence, never silently assumed stable.
    """
    # Evaluate only evidence observed by the requested window end. Appending
    # future samples must not retroactively turn an unresolved window into PASS.
    if until is not None:
        stop=bisect_right(t,until+1e-12)
        t,y=t[:stop],y[:stop]
    out=array("B",[int(y[0])])*len(y); events=[]; pending=[]
    state=int(y[0]); run=1; write_from=0
    while run<len(y):
        stop=run+1
        while stop<len(y) and y[stop]==y[run]: stop+=1
        level=int(y[run]); finish=t[stop] if stop<len(y) else t[-1]
        if level!=state:
            if finish-t[run]+1e-12>=hold:
                for k in range(write_from,run): out[k]=state
                events.append({"time_s":t[run],"from":state,"to":level,"confirmed_s":t[run]+hold})
                state=level; write_from=run
            elif stop==len(y): pending.append(t[run])
        run=stop
    for k in range(write_from,len(y)): out[k]=state
    return out,events,pending


def event_support(record,sig,event,max_gap=None):
    """An edge needs a valid preceding sample and continuous qualification data."""
    i=bisect_left(sig.time,event["time_s"])
    j=min(len(sig.time)-1,bisect_left(sig.time,event["confirmed_s"]))
    coverage(record,sig,sig.time[max(0,i-1)],sig.time[max(i,j)],max_gap)


def trigger_time(record,config,test,cache):
    tr=test.get("trigger",{"type":"time","at":0})
    if tr["type"]=="time": return float(tr["at"]),{"type":"time","at":tr["at"]}
    alias=tr["channel"]; sig=cache.get(alias)
    if sig is None: sig=resolve(record,alias,config);cache[alias]=sig
    start,end=tr.get("search",[sig.time[0],sig.time[-1]])
    if start<sig.time[0]-1e-9 or end>sig.time[-1]+1e-9 or start>=end:
        raise InsufficientData("触发搜索窗口超出录波范围或无效")
    hits=[];qualified=[];metadata={}
    if tr["type"]=="digital_edge":
        if "noise" in tr:
            _,events,pending,rejected=digital_qualify(sig.time,sig.values,tr["noise"],end)
            # Quality is scoped below to the selected occurrence's causal prefix.
            metadata["noise_scope"]="搜索开始至所选事件确认样本"
        else:
            _,events,pending=debounced(sig.time,sig.values,tr.get("debounce_s",0),until=end)
        desired=1 if tr.get("edge","rising")=="rising" else 0
        for e in events:
            if e["to"]==desired and start-1e-12<=e["time_s"]<=end+1e-12 and e["confirmed_s"]<=end+1e-12:
                hits.append(e["time_s"]);qualified.append(e)
    elif "hysteresis" in tr or "hold_s" in tr:
        direction=1 if tr.get("edge","rising")=="rising" else -1
        events,metadata=trigger_crossings(sig.time,sig.values,start,end,tr["threshold"],direction,tr.get("hysteresis",0),tr.get("hold_s",0))
        for e in events:
            hits.append(e["time_s"]);qualified.append(e)
    else:
        direction=1 if tr.get("edge","rising")=="rising" else -1; threshold=tr["threshold"]
        # Search is inclusive: retain the predecessor of its left boundary.
        # A sample already at threshold is not a new crossing without that proof.
        t,y=sig.time,sig.values
        for i in range(max(1,bisect_left(t,start-1e-12)),min(len(t),bisect_right(t,end+1e-12)+1)):
            v0,v1=direction*(y[i-1]-threshold),direction*(y[i]-threshold)
            if v0<0<=v1:
                hit=t[i-1]+(t[i]-t[i-1])*(-v0)/(v1-v0)
                if start-1e-12<=hit<=end+1e-12:
                    hits.append(hit);qualified.append({"support_s":[t[i-1],t[i]],"time_s":hit,"confirmed_s":hit})
    occurrence=tr.get("occurrence",1)
    if len(hits)<occurrence:
        coverage(record,sig,start,end,test.get("max_gap_s"))
        raise InsufficientData(f"未找到第 {occurrence} 个指定触发事件；请确认试验激励是否执行")
    event=qualified[occurrence-1]
    # A later missing block cannot invalidate an earlier fully observed trigger.
    # A missing block BEFORE the chosen occurrence still makes its ordinal unknown.
    j=min(len(sig.time)-1,bisect_left(sig.time,event.get("confirmation_sample_s",event["confirmed_s"])))
    cutoff=sig.time[j]
    coverage(record,sig,start,max(start+1e-12,cutoff),test.get("max_gap_s"))
    for selected in qualified[:occurrence]:event_support(record,sig,selected,test.get("max_gap_s"))
    if tr["type"]=="digital_edge" and "noise" in tr:
        qa=digital_audit(sig.time,sig.values,events,rejected,start,cutoff,tr["noise"])
        if qa["rejected_edges"]>tr["noise"].get("max_rejected_edges",20) or qa["longest_chatter_s"]>tr["noise"]["max_chatter_s"]+1e-12:
            raise InsufficientData("数字触发通道噪声预算超限；不能可靠定位试验激励")
        metadata["digital_noise"]=qa
    metadata["validated_prefix_end_s"]=cutoff
    metadata["hits_note"]="hits 为整个搜索范围的候选数；仅所选 occurrence 及其之前证据已校验"
    idx=bisect_left(sig.time,event["time_s"])
    support=event.get("support_s",[sig.time[max(0,idx-1)],sig.time[min(len(sig.time)-1,idx)]])
    return hits[occurrence-1],{"type":tr["type"],"channel":alias,"occurrence":occurrence,"hits":len(hits),"at":hits[occurrence-1],
                              "support_s":support,"uncertainty_s":support[1]-support[0],"qualified_event":event,**metadata}


def criterion(name,value,minimum=None,maximum=None,guard=0.):
    if value is None: return {"metric":name,"value":None,"min":minimum,"max":maximum,"status":"INCONCLUSIVE","reason":"未取得可用测量值"}
    number(value,name)  # NaN/Infinity must never satisfy a comparison by accident.
    eps=1e-9*max(1.,abs(value),abs(maximum or 0),abs(minimum or 0))
    failed=(minimum is not None and value<minimum-eps) or (maximum is not None and value>maximum+eps)
    ambiguous=guard>0 and ((minimum is not None and abs(value-minimum)<=guard) or (maximum is not None and abs(value-maximum)<=guard))
    return {"metric":name,"value":value,"min":minimum,"max":maximum,
            "status":"INCONCLUSIVE" if ambiguous else ("FAIL" if failed else "PASS"),
            "reason":"位于配置的测量保护带内，需人工复核" if ambiguous else ("超出限值" if failed else "满足限值")}


def overall(items):
    states=[i["status"] for i in items]
    for s in ("ERROR","FAIL","INCONCLUSIVE"):
        if s in states: return s
    return "PASS" if states else "ERROR"


def compact_trace(t,y,limit=900):
    """Bounded extrema display, never used for assertions. None breaks paths.

    A downsampling bucket containing missing values is deliberately not joined;
    a closer viewport recovers the valid samples around the missing interval.
    """
    if limit<8: raise ValueError("trace limit must be at least 8")
    n=len(t)
    if n!=len(y): raise ValueError("trace time/value lengths differ")
    if n<=limit:
        return [[float(t[i]),float(y[i]) if math.isfinite(y[i]) else None] for i in range(n)]
    bucket=math.ceil(n/max(1,(limit-2)//4)); out=[]
    for j in range(0,n,bucket):
        stop=min(j+bucket,n)
        if any(not math.isfinite(y[i]) for i in range(j,stop)):
            out.append([float(t[j]),None])
            if stop-1!=j: out.append([float(t[stop-1]),None])
        else:
            ids={j,stop-1,min(range(j,stop),key=lambda k:y[k]),max(range(j,stop),key=lambda k:y[k])}
            out.extend([[float(t[i]),float(y[i])] for i in sorted(ids)])
    return out


def analyze_one(record,config,a,cache):
    if a["type"]=="analog_relation":
        from .relation import analyze_relation
        return analyze_relation(record,config,a,cache)
    if "robust" in a:return analyze_robust_step(record,config,a,cache)
    alias=a["channel"]; sig=cache.get(alias)
    if sig is None: sig=resolve(record,alias,config);cache[alias]=sig
    t0,trig=trigger_time(record,config,a,cache)
    start,end=t0+a["window"][0],t0+a["window"][1]
    lo,hi=coverage(record,sig,start,end,a.get("max_gap_s"))
    checks=[]; metrics={}; evidence={"trigger":trig,"window_absolute_s":[start,end],"mapping":sig.mapping,
                               "max_sample_interval_s":max(sig.time[i]-sig.time[i-1] for i in range(lo+1,hi))}
    guard=a.get("guard_band_s",0.)
    typ=a["type"]
    if typ.startswith("digital"):
        if "noise" in a:
            nr=a["noise"]
            filtered,events,pending,rejected=digital_qualify(sig.time,sig.values,nr,end)
            audit=digital_audit(sig.time,sig.values,events,rejected,start,end,nr)
            evidence["digital_noise"]=audit
            if nr.get("timing_guard",True):
                guard=max(guard,evidence["max_sample_interval_s"]+trig.get("uncertainty_s",0))
        else:
            filtered,events,pending=debounced(sig.time,sig.values,a.get("debounce_s",0.),until=end)
        events_in=[e for e in events if start-1e-12<=e["time_s"]<=end+1e-12]
        if any(p<=end+1e-12 for p in pending) or any(e["time_s"]<=end+1e-12 and e["confirmed_s"]>end+1e-12 for e in events):
            raise InsufficientData("窗口末端存在尚未满足去抖保持时间的数字量变位")
        for event in events_in:
            event_support(record,sig,event,a.get("max_gap_s"))
        at_start=max(0,bisect_right(sig.time,start)-1); at_end=bisect_right(sig.time,end)
        tt=list(sig.time[at_start:at_end]); yy=list(filtered[at_start:at_end])
        if typ=="digital_edge":
            edge=a.get("edge","rising")
            matched=[e for e in events_in if edge=="any" or e["to"]==(1 if edge=="rising" else 0)]
            checks.append(criterion("edge_count",len(matched),a.get("count",1),a.get("count",1)))
            latency=[e["time_s"]-t0 for e in matched]; metrics={"edge_count":len(matched),"latencies_s":latency}
            if matched and ("latency_min_s" in a or "latency_max_s" in a):
                for i,x in enumerate(latency): checks.append(criterion(f"latency_{i+1}_s",x,a.get("latency_min_s"),a.get("latency_max_s"),guard))
            evidence["events"]=matched[:1000]
        elif typ=="digital_hold":
            bad=[i for i,v in enumerate(yy) if v!=a["value"]]
            checks.append(criterion("violating_samples",len(bad),0,0));metrics={"expected":a["value"],"violating_samples":len(bad)}
            if bad: evidence["first_violation_s"]=max(start,tt[bad[0]])
        else:
            active=a.get("active",1)
            # Pulses must have both observed edges wholly within the analysis window.
            if filtered[at_start]==active and not any(abs(e["time_s"]-start)<1e-12 and e["to"]==active for e in events_in):
                raise InsufficientData("脉冲已在窗口开始前置位，缺少完整前沿")
            opened=None; pulses=[]
            for e in events_in:
                if e["to"]==active: opened=e["time_s"]
                elif opened is not None:
                    pulses.append({"start_s":opened,"end_s":e["time_s"],"width_s":e["time_s"]-opened});opened=None
            if opened is not None: raise InsufficientData("脉冲在窗口结束时尚未复位，无法计算完整脉宽")
            checks.append(criterion("pulse_count",len(pulses),a.get("count",1),a.get("count",1)))
            for i,p in enumerate(pulses): checks.append(criterion(f"pulse_{i+1}_width_s",p["width_s"],a.get("min_width_s"),a.get("max_width_s"),guard))
            metrics={"pulse_count":len(pulses),"widths_s":[p["width_s"] for p in pulses]};evidence["pulses"]=pulses[:1000]
        if "noise" in a:
            for key,maximum in (("rejected_edges",nr.get("max_rejected_edges",20)),("longest_chatter_s",nr["max_chatter_s"])):
                qc=criterion(key,audit[key],None,maximum)
                if qc["status"]=="FAIL":qc.update(status="INCONCLUSIVE",reason="数字量抑制边沿或抖动时长超过预算；不能静默当作稳定状态")
                checks.append(qc)
            evidence["processed_trace"]=compact_trace(sig.time[lo:min(hi,len(filtered))],filtered[lo:min(hi,len(filtered))])
            evidence["timing_review_guard_s"]=guard
        evidence["debounce_s"]=a.get("debounce_s",0)
        evidence["raw_trace"]=compact_trace(sig.time[lo:hi],sig.values[lo:hi])
        evidence["trace_kind"]="digital"
    else:
        tt,yy=segment(record,sig,start,end,a.get("max_gap_s"))
        evidence["raw_trace"]=compact_trace(tt,yy);evidence["trace_kind"]="analog";evidence["unit"]=sig.unit
        if typ in ("analog_range","analog_rms"):
            if typ=="analog_range":
                metrics={"min":min(yy),"max":max(yy),"mean":mean_time(tt,yy)}
                checks=[criterion("minimum",min(yy),a.get("min"),None),criterion("maximum",max(yy),None,a.get("max"))]
            else:
                v=rms_time(tt,yy);metrics={"rms":v};checks=[criterion("rms",v,a.get("min"),a.get("max"))]
        else:
            bt,by=segment(record,sig,t0+a["baseline_window"][0],t0+a["baseline_window"][1],a.get("max_gap_s"))
            if len(bt)<3: raise InsufficientData("基线窗口至少需要 3 个有效时间点")
            baseline=mean_time(bt,by)
            if "target" in a: target=float(a["target"])
            else:
                target_sig=resolve(record,a["target_channel"],config)
                if target_sig.unit!=sig.unit: raise ConfigError("目标通道与反馈通道原始单位不同；需先统一量纲")
                tw=a["target_window"]
                target_t,target_y=segment(record,target_sig,t0+tw[0],t0+tw[1],a.get("max_gap_s"))
                if max(target_y)-min(target_y)>a.get("target_tolerance",0)+1e-9:
                    raise InsufficientData("目标值窗口不是恒定设定值；请拆分多阶跃用例或配置合理 target_tolerance")
                target=mean_time(target_t,target_y);evidence["target_mapping"]=target_sig.mapping
            delta=target-baseline
            if abs(delta)<=1e-12*max(1.,abs(target),abs(baseline)): raise InsufficientData("目标与基线没有有效阶跃；不能定义阶跃响应指标")
            norm=a.get("normalization","step")
            denom=abs(delta) if norm=="step" else (abs(target) if norm=="target" else a["rated"])
            if denom<=1e-12: raise ConfigError("百分比归一化分母为零；零目标请改用 step 或 rated")
            direction=1 if delta>0 else -1
            progress=[(v-baseline)/delta for v in yy]
            def crossing(fraction):
                # A response already beyond threshold at trigger is not a measured delay.
                if progress[0]>=fraction: return None
                for i in range(1,len(tt)):
                    if progress[i-1]<fraction<=progress[i]:
                        return tt[i-1]+(tt[i]-tt[i-1])*(fraction-progress[i-1])/(progress[i]-progress[i-1])-t0
                return None
            low=crossing(a.get("low_fraction",.1)); high=crossing(a.get("high_fraction",.9))
            band=denom*a.get("settle_band_pct",2)/100
            outside=[i for i,v in enumerate(yy) if abs(v-target)>band+1e-12]
            ix=(outside[-1]+1) if outside else 0
            settled=(tt[ix]-t0) if ix<len(tt) else None
            hold=a.get("settle_hold_s",.1)
            settling_observed=settled is not None and end-(t0+settled)+1e-12>=hold
            st,sy=segment(record,sig,end-a.get("steady_last_s",.2),end,a.get("max_gap_s"))
            if len(st)<3: raise InsufficientData("稳态窗口至少需要 3 个有效时间点")
            steady_mean=mean_time(st,sy)
            peak_index=max(range(len(yy)),key=lambda i:direction*yy[i])
            metrics={"baseline":baseline,"target":target,"step_amplitude":delta,"normalization":norm,"denominator":denom,
                "overshoot_pct":max(0.,direction*(yy[peak_index]-target))/denom*100,
                "peak":yy[peak_index],"peak_time_s":tt[peak_index]-t0,
                "latency_s":low,"response_s":high,"rise_s":None if low is None or high is None else high-low,
                "settling_s":settled if settling_observed else None,"steady_mean":steady_mean,
                "steady_error_pct":abs(steady_mean-target)/denom*100,
                "steady_ripple_pp":max(sy)-min(sy),"settle_band_absolute":band}
            evidence.update({"baseline_window_s":[bt[0],bt[-1]],"steady_window_s":[st[0],st[-1]],
                             "settle_hold_s":hold,"target":target,"band_absolute":band,
                             "definitions":"响应时间=触发至首次高阈值穿越；上升时间=低阈值至高阈值；稳态均值为时间加权均值；调节时间需持续在带内直到窗口结束"})
            for limit,maximum in a["limits"].items():
                metric=limit[:-4]; value=metrics[metric]
                c=criterion(metric,value,None,maximum,guard if metric.endswith("_s") else 0)
                if value is None:
                    if metric=="rise_s":
                        # Rise time starts at the low crossing, not at the trigger.
                        sufficient=low is not None and end-t0+1e-12>=low+maximum
                    else:
                        sufficient=end-t0+1e-12>=maximum+(hold if metric=="settling_s" else 0)
                    pre_responded=progress[0]>=a.get("low_fraction",.1)
                    if sufficient and not pre_responded:
                        c["status"]="FAIL";c["reason"]="充分观察时间内未达到要求"
                    else: c["reason"]="观察时间不足或触发前已响应，不能可靠测量"
                checks.append(c)
    return metrics,checks,evidence


def finite_payload(value):
    """Fail closed before serialization if an intermediate calculation overflows."""
    if isinstance(value,float) and not math.isfinite(value):
        raise ConfigError("计算出现非有限结果或溢出；请复核信号量级、量纲与配置")
    if isinstance(value,dict):
        for child in value.values():finite_payload(child)
    elif isinstance(value,(list,tuple)):
        for child in value:finite_payload(child)


def run(record: Record, config: dict) -> dict:
    validate(config)
    cache={}; results=[]
    for a in config["tests"]:
        item={"id":a["id"],"name":a.get("name",a["id"]),"type":a["type"],"channel":a["channel"],
              "standard":a.get("standard",config.get("standard",{})),"metrics":{},"checks":[],"evidence":{}}
        try:
            m,c,e=analyze_one(record,config,a,cache)
            finite_payload(m);finite_payload(c);finite_payload(e)
            item.update({"metrics":m,"checks":c,"evidence":e,"status":overall(c),"reason":"；".join(dict.fromkeys(x["reason"] for x in c if x["status"]!="PASS")) or "全部断言满足配置限值"})
        except InsufficientData as exc: item.update(status="INCONCLUSIVE",reason=str(exc))
        except (ConfigError,ValueError,KeyError,OverflowError,ZeroDivisionError) as exc: item.update(status="ERROR",reason=str(exc))
        results.append(item)
    canonical=json.dumps(config,sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode("utf-8")
    counts={s:sum(r["status"]==s for r in results) for s in ("PASS","FAIL","INCONCLUSIVE","ERROR")}
    return {"product":"ESS WaveTest","version":__version__,"generated_utc":datetime.now(timezone.utc).isoformat(),
            "suite":config["name"],"status":overall(results),"counts":counts,"record":record.summary(),
            "configuration":config,"config_sha256":hashlib.sha256(canonical).hexdigest(),
            "results":results,"notice":"结果仅表示满足当前配置断言，不代表已取得国标符合性认证。示例阈值不属于标准原文。"}
