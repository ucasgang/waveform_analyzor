"""Reproducible synthetic COMTRADE fixtures. No captured customer data."""
from __future__ import annotations
from datetime import datetime, timedelta
import csv
import io
import json
import math
from pathlib import Path
import random
import struct

FORMATS=("ASCII","BINARY","BINARY32","FLOAT32")


def write_record(folder: str|Path, name: str, times: list[float], analog: list[dict], digital: list[dict],
                 fmt="BINARY", revision=2013, timemult=1., rates=None,
                 encoding="utf-8", zero_timestamps=False, trigger_s=.2,
                 raw_override=None, omit_rates=False) -> Path:
    """Independent fixture writer. Channel data contain engineering values;
    CFG a/b are inverted only here to encode quantized raw values.
    """
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    if len(times)<2: raise ValueError("need at least two times")
    n=len(times); na=len(analog);nd=len(digital)
    if any(len(c["data"])!=n for c in analog+digital): raise ValueError("channel length mismatch")
    if fmt in ("BINARY32","FLOAT32") and revision!=2013: raise ValueError("32-bit formats need 2013")
    rates=[] if omit_rates else (rates if rates is not None else [(1/(times[1]-times[0]),n)])
    rows=[["ESS LAB","SIMULATOR"]+([] if revision==1991 else [str(revision)]),[str(na+nd),f"{na}A",f"{nd}D"]]
    raw_channels=[]
    for i,c in enumerate(analog,1):
        a=c.get("a",.001 if fmt!="BINARY" else .01);b=c.get("b",0)
        lo,hi=(-32767,32767) if fmt=="BINARY" else (-2147483647,2147483647)
        if fmt=="FLOAT32": lo,hi=-1e30,1e30
        row=[i,c["name"],c.get("phase",""),"PCS",c.get("unit","kW"),a,b,c.get("skew_us",0),c.get("raw_min",lo),c.get("raw_max",hi)]
        if revision!=1991: row += [c.get("primary",1),c.get("secondary",1),c.get("side","P")]
        rows.append(row)
        vals=[]
        for value in c["data"]:
            if value is None or not math.isfinite(value):
                v={"ASCII":"" if revision==1991 else 99999,"BINARY":-1 if revision==1991 else -32768,"BINARY32":-2147483648,"FLOAT32":-3.4028234663852886e38}[fmt]
            else:
                x=(value-b)/a
                v=x if fmt=="FLOAT32" else round(x)
                if fmt=="BINARY" and not -32767<=v<=32767: raise ValueError("binary16 overflow: adjust fixture scaling")
                if fmt=="ASCII" and revision!=1991 and v==99999: raise ValueError("ASCII missing-value sentinel collision")
            vals.append(v)
        raw_channels.append(vals)
    for i,c in enumerate(digital,1):
        rows.append([i,c["name"],c.get("normal",0)] if revision==1991 else [i,c["name"],"","PCS",c.get("normal",0)])
    rows += [[50],[len(rates)]]
    rows += [[f"{r:.12g}",end] for r,end in rates]
    base=datetime(2026,1,1)
    df="%m/%d/%Y" if revision==1991 else "%d/%m/%Y"
    rows += [[base.strftime(df),base.strftime("%H:%M:%S.%f")],[(base+timedelta(seconds=trigger_s)).strftime(df),(base+timedelta(seconds=trigger_s)).strftime("%H:%M:%S.%f")],[fmt]]
    if revision!=1991: rows += [[timemult]]
    if revision==2013: rows += [["+00:00","+00:00"],["0","0"]]
    buf=io.StringIO();csv.writer(buf,lineterminator="\r\n").writerows(rows)
    cfg=folder/f"{name}.cfg";cfg.write_bytes(buf.getvalue().encode(encoding))
    if fmt=="ASCII":
        with (folder/f"{name}.dat").open("w",newline="",encoding="ascii") as f:
            w=csv.writer(f,lineterminator="\r\n")
            for j,t in enumerate(times):
                row=[j+1,0 if zero_timestamps else round(t*1e6/timemult)]+[v[j] for v in raw_channels]+[c["data"][j] for c in digital]
                if raw_override: row=raw_override(j,row)
                w.writerow(row)
    else:
        st=struct.Struct("<II"+{"BINARY":"h","BINARY32":"i","FLOAT32":"f"}[fmt]*na+"H"*((nd+15)//16))
        with (folder/f"{name}.dat").open("wb") as f:
            for j,t in enumerate(times):
                words=[sum(int(digital[k]["data"][j])<<(k%16) for k in range(g*16,min((g+1)*16,nd))) for g in range((nd+15)//16)]
                row=[j+1,0 if zero_timestamps else round(t*1e6/timemult)]+[v[j] for v in raw_channels]+words
                if raw_override: row=raw_override(j,row)
                f.write(st.pack(*row))
    return cfg


def demo_suite() -> dict:
    trigger={"type":"digital_edge","channel":"command","edge":"rising","debounce_s":.002,"search":[0,1]}
    return {"schema_version":1,"name":"储能 PCS 厂内功能与阶跃响应验收 · 演示",
        "description":"所有阈值为教学示例，不是国标条款的默认值。时间单位统一为秒。",
        "standard":{"reference":"DEMO / 项目自定义验收配置","clause":"教学示例，非规范条款","reviewed":False,"note":"正式验收需按适用标准及合同逐条复核阈值、基准与测量方法。"},
        "channels":{"command":{"kind":"digital","name":"启动命令"},"ready":{"kind":"digital","name":"运行反馈"},
                    "pulse":{"kind":"digital","name":"合闸脉冲"},"alarm":{"kind":"digital","name":"故障告警"},
                    "power":{"kind":"analog","name":"有功功率","unit":"kW"},
                    "dc":{"kind":"analog","name":"直流母线电压","unit":"V"},
                    "voltage":{"kind":"analog","name":"交流电压Ua","unit":"V"}},
        "tests":[
          {"id":"D01","name":"运行反馈正确置位","type":"digital_edge","channel":"ready","trigger":trigger,"window":[0,.4],"edge":"rising","count":1,"debounce_s":.003,"latency_min_s":.01,"latency_max_s":.1},
          {"id":"D02","name":"合闸脉冲宽度","type":"digital_pulse","channel":"pulse","trigger":trigger,"window":[0,.4],"active":1,"count":1,"min_width_s":.03,"max_width_s":.08},
          {"id":"D03","name":"试验期间无故障告警","type":"digital_hold","channel":"alarm","trigger":trigger,"window":[0,1.5],"value":0},
          {"id":"A01","name":"有功功率阶跃响应","type":"analog_step","channel":"power","trigger":trigger,"window":[0,1.5],"baseline_window":[-.15,-.02],"target":100,"normalization":"rated","rated":100,"settle_band_pct":2,"settle_hold_s":.1,"steady_last_s":.25,
           "limits":{"overshoot_pct_max":5,"response_s_max":.4,"rise_s_max":.4,"settling_s_max":.7,"steady_error_pct_max":1}},
          {"id":"A02","name":"直流母线电压范围","type":"analog_range","channel":"dc","trigger":trigger,"window":[0,1.5],"min":680,"max":720},
          {"id":"A03","name":"交流电压有效值","type":"analog_rms","channel":"voltage","trigger":trigger,"window":[.8,1.2],"min":215,"max":225}
        ]}


def scenario(folder, name="demo_pass", fmt="BINARY", variant="pass", seed=42, revision=2013,
             extra_analog=0,extra_digital=0, dt=.001,duration=2.0,zero_timestamps=False):
    rng=random.Random(seed)
    t=[i*dt for i in range(round(duration/dt)+1)]
    t0=.2; tau=.08 if variant!="slow" else .5
    target=-100 if variant=="falling" else (0 if variant=="zero_target" else 100)
    initial=100 if variant=="zero_target" else (50 if variant=="falling" else 0)
    amplitude=target-initial
    power=[]
    for x in t:
        if x<t0: y=initial
        else:
            u=x-t0
            y=initial+amplitude*(1-math.exp(-u/tau))
            if variant=="overshoot": y+=amplitude*.20*math.exp(-((u-.36)/.07)**2)
            if variant=="steady_error": y+=5*(1-math.exp(-u/.08))
            if variant=="never_response": y=initial
            if variant=="oscillation": y+=8*math.sin(2*math.pi*8*u)*(1-math.exp(-u/.1))
            if variant=="noise": y+=rng.uniform(-.1,.1)
        power.append(y)
    if variant=="missing": power[700]=None
    analog=[{"name":"有功功率","unit":"kW","a":.01,"data":power},
            {"name":"直流母线电压","unit":"V","a":.1,"data":[700+2*math.sin(2*math.pi*10*x) for x in t]},
            {"name":"交流电压Ua","unit":"V","a":.02,"data":[220*math.sqrt(2)*math.sin(2*math.pi*50*x) for x in t]}]
    cmd=[int(x>=t0) for x in t]
    ready=[int(x>=t0+.04) for x in t]
    pulse=[int(t0+.05<=x<t0+(.16 if variant=="wide_pulse" else .1)) for x in t]
    alarm=[int(variant=="alarm" and .8<=x<.81) for x in t]
    if variant=="no_trigger": cmd=[0]*len(t)
    if variant=="missing_edge": ready=[0]*len(t)
    if variant=="bounce":
        for i,x in enumerate(t):
            if .21<=x<.211 or .22<=x<.221: ready[i]=1
    if variant=="open_pulse": pulse=[int(x>=.25) for x in t]
    digital=[{"name":"启动命令","data":cmd},{"name":"运行反馈","data":ready},{"name":"合闸脉冲","data":pulse},{"name":"故障告警","data":alarm}]
    for j in range(extra_analog): analog.append({"name":f"备用模拟量_{j+1:03d}","unit":"V","a":.02,"data":[10+math.sin(x*(j%20+1)) for x in t]})
    for j in range(extra_digital): digital.append({"name":f"备用数字量_{j+1:03d}","data":[int((int(x*10)+j)%3==0) for x in t]})
    if variant=="gap": t=[x if x<.7 else x+.01 for x in t]
    config=demo_suite()
    config["name"] += f" / {name}"
    if variant in ("falling","zero_target"): config["tests"][3]["target"]=target
    cfg=write_record(folder,name,t,analog,digital,fmt,revision,encoding="gb18030" if variant=="gbk" else "utf-8",zero_timestamps=zero_timestamps)
    case=Path(folder)/f"{name}.json";case.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding="utf-8")
    return cfg,case


def generate_examples(folder: str|Path) -> dict:
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    specs=[("01_pass_binary","BINARY","pass","PASS",2013),
           ("02_pass_ascii","ASCII","pass","PASS",1999),
           ("03_pass_binary32","BINARY32","pass","PASS",2013),
           ("04_pass_float32","FLOAT32","pass","PASS",2013),
           ("05_slow_response","BINARY","slow","FAIL",2013),
           ("06_overshoot","FLOAT32","overshoot","FAIL",2013),
           ("07_steady_error","BINARY","steady_error","FAIL",2013),
           ("08_wide_pulse","BINARY","wide_pulse","FAIL",2013),
           ("09_alarm_glitch","BINARY","alarm","FAIL",2013),
           ("10_no_trigger","BINARY","no_trigger","INCONCLUSIVE",2013),
           ("11_missing_value","FLOAT32","missing","INCONCLUSIVE",2013),
           ("12_sampling_gap","BINARY","gap","INCONCLUSIVE",2013),
           ("13_falling_step","BINARY","falling","PASS",2013),
           ("14_zero_target","BINARY","zero_target","PASS",2013),
           ("15_contact_bounce","BINARY","bounce","PASS",2013),
           ("16_open_pulse","BINARY","open_pulse","INCONCLUSIVE",2013),
           ("17_never_response","BINARY","never_response","FAIL",2013),
           ("18_oscillation","BINARY","oscillation","FAIL",2013),
           ("19_measurement_noise","FLOAT32","noise","PASS",2013),
           ("20_chinese_gbk","BINARY","gbk","PASS",1999),
           ("21_missing_edge","BINARY","missing_edge","FAIL",2013)]
    cases=[]
    for name,fmt,variant,expected,revision in specs:
        cfg,case=scenario(folder,name,fmt,variant,revision=revision)
        cases.append({"cfg":cfg.name,"case":case.name,"expected":expected,"variant":variant})
    cfg,case=scenario(folder,"22_512_channels",extra_analog=125,extra_digital=380)
    cases.append({"cfg":cfg.name,"case":case.name,"expected":"PASS","variant":"512 channels / 128 analog + 384 digital"})
    cfg,case=scenario(folder,"23_rebuilt_time",zero_timestamps=True)
    cases.append({"cfg":cfg.name,"case":case.name,"expected":"PASS","variant":"all-zero timestamps"})
    manifest={"description":"预期包含 FAIL / INCONCLUSIVE，它们被正确识别才表示回归通过。","cases":cases}
    (folder/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    (folder/"starter_case.json").write_text(json.dumps(demo_suite(),ensure_ascii=False,indent=2),encoding="utf-8")
    return manifest
