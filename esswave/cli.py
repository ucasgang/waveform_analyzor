"""GUI, CLI and batch entry points. Exit codes: 0 pass; 1 fail; 2 error; 3 inconclusive."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import platform
import sys
import time
from . import __version__
from .comtrade import load,ComtradeError
from .engine import run,ConfigError
from .generate import generate_examples
from .complex_cases import generate_complex
from .noise_cases import generate_noise
from .field_cases import generate_field
from .batchwork import plan_files,strict_json
from .report import export,atomic_text

EXIT={"PASS":0,"FAIL":1,"ERROR":2,"INCONCLUSIVE":3}

def read_config(path):
    p=Path(path)
    if p.stat().st_size>2*1024**2:raise ConfigError("配置文件超过 2 MiB")
    def reject_constant(value):raise ConfigError(f"JSON 不允许 {value}")
    return strict_json(p.read_bytes())


def run_file(cfg,case,out,dat=None):
    record=load(cfg,dat)
    result=run(record,read_config(case))
    export(result,out,record=record)
    return result


def batch(manifest_path=None,directory=None,case=None,out="batch_results",recursive=False,sibling_cases=False):
    output=Path(out);output.mkdir(parents=True,exist_ok=True)
    fixed_config=None
    if manifest_path:
        mp=Path(manifest_path);data=read_config(mp);entries=data.get("cases",[]);base=mp.parent
        if not entries:raise ConfigError("清单 cases 不能为空")
    else:
        base=Path(directory)
        if not base.is_dir():raise ConfigError("目录不存在")
        if sibling_cases and case:raise ConfigError("--sibling-cases 与 --case 不能同时使用")
        if not sibling_cases and not case:raise ConfigError("目录批处理需要 --case 或 --sibling-cases")
        paths=sorted(base.rglob('*') if recursive else base.iterdir())
        paths=[p for p in paths if p.is_file() and p.suffix.lower() in ('.cfg','.dat','.json')]
        listing=[{"path":str(p.relative_to(base).as_posix()),"size":p.stat().st_size} for p in paths]
        plan=plan_files(listing,"sibling" if sibling_cases else "common",browser_limits=False)
        fixed_config=read_config(case) if not sibling_cases else None
        entries=[]
        for row in plan['rows']:
            entries.append({"cfg":row['cfg'] or row['path'],"dat":row['dat'],
                "case":row['case'] or (str(Path(case).resolve()) if case else ''),
                "preflight_error":row['reason'] if row['status']=='ERROR' else '',"source_relative":row['path']})
    summary=[]
    for n,entry in enumerate(entries,1):
        cfg=base/entry["cfg"];cp=base/entry["case"]
        folder=output/f"{n:03d}_{cfg.stem}"
        try:
            if entry.get("preflight_error"):raise ConfigError(entry['preflight_error'])
            dat=base/entry['dat'] if entry.get('dat') else None
            if fixed_config is not None:
                record=load(cfg,dat);r=run(record,fixed_config);export(r,folder,record=record)
            else:r=run_file(cfg,cp,folder,dat=dat)
            status=r["status"];reason=""
        except (ValueError,OSError,KeyError,TypeError) as exc:
            status="ERROR";reason=str(exc)
            atomic_text(folder/"error.json",json.dumps({"status":status,"error":reason,"cfg":str(cfg)},ensure_ascii=False,indent=2))
        expected=entry.get("expected")
        row={"cfg":str(cfg),"case":str(cp),"status":status,"expected":expected,
             "matched_expectation":None if expected is None else status==expected,"reason":reason,"report_dir":str(folder)}
        summary.append(row);print(f"{n:03d} {cfg.name}: {status}"+(f" (expected {expected})" if expected else ""))
    regression=all(row["matched_expectation"] is True for row in summary) if all(row["expected"] is not None for row in summary) else None
    if regression is not None:status="PASS" if regression else "FAIL"
    else:
        statuses={x["status"] for x in summary};status=next((s for s in ("ERROR","FAIL","INCONCLUSIVE") if s in statuses),"PASS")
    data={"mode":"expected_outcome_regression" if regression is not None else "acceptance","status":status,"matched_all_expectations":regression,"records":len(summary),"cases":summary,
          "notice":"清单回归通过只表示实际判定符合预期；不是所有被测设备通过。"}
    atomic_text(output/"summary.json",json.dumps(data,ensure_ascii=False,indent=2))
    return data


def main(argv=None):
    parser=argparse.ArgumentParser(prog="ESS_WaveTest",description="储能 COMTRADE 离线自动验收；示例限值不是国标原文。")
    parser.add_argument("--version",action="version",version=__version__)
    sub=parser.add_subparsers(dest="command")
    gui=sub.add_parser("gui",help="启动本机中文图形界面");gui.add_argument("--port",type=int,default=8765);gui.add_argument("--no-browser",action="store_true")
    one=sub.add_parser("run",help="运行一组 CFG/DAT 及 JSON 用例")
    one.add_argument("--cfg",required=True);one.add_argument("--dat");one.add_argument("--case",required=True);one.add_argument("--out",default="results")
    ins=sub.add_parser("inspect",help="列出录波元数据、通道及质量警告");ins.add_argument("cfg");ins.add_argument("--dat")
    gen=sub.add_parser("generate",help="重新生成 23 组教学录波与用例");gen.add_argument("--out",default="examples")
    cg=sub.add_parser("generate-complex",help="重新生成 12 组复杂联动工况");cg.add_argument("--out",default="examples/complex")
    fg=sub.add_parser("generate-field",help="生成18组分层耦合场景（非现场录波）");fg.add_argument("--out",default="examples/field");fg.add_argument("--seed",type=int,default=20261011)
    ng=sub.add_parser("generate-noise",help="重新生成 32 组噪声与扰动工况");ng.add_argument("--out",default="examples/noise")
    many=sub.add_parser("batch",help="清单回归或目录批量验收")
    group=many.add_mutually_exclusive_group(required=True);group.add_argument("--manifest");group.add_argument("--directory")
    many.add_argument("--case");many.add_argument("--out",default="batch_results")
    many.add_argument("--recursive",action="store_true",help="包含所有子目录")
    many.add_argument("--sibling-cases",action="store_true",help="每组使用同目录同名JSON；不与--case同时使用")
    args=parser.parse_args(argv)
    if sys.version_info<(3,10):parser.error("需要 Python 3.10 或更新版本")
    try:
        if args.command in (None,"gui"):
            from .server import serve
            serve(getattr(args,"port",8765),not getattr(args,"no_browser",False));return 0
        if args.command=="inspect":
            print(json.dumps(load(args.cfg,args.dat).summary(),ensure_ascii=False,indent=2));return 0
        if args.command=="generate":
            data=generate_examples(args.out);print(f"已生成 {len(data['cases'])} 组录波：{Path(args.out).resolve()}");return 0
        if args.command=="generate-field":
            data=generate_field(args.out,args.seed);print(f"已生成 {len(data['cases'])} 组分层耦合合成录波：{Path(args.out).resolve()}");return 0
        if args.command=="generate-noise":
            data=generate_noise(args.out);print(f"已生成 {len(data['cases'])} 组噪声录波：{Path(args.out).resolve()}");return 0
        if args.command=="generate-complex":
            data=generate_complex(args.out);print(f"已生成 {len(data['cases'])} 组复杂录波：{Path(args.out).resolve()}");return 0
        if args.command=="run":
            t=time.perf_counter();r=run_file(args.cfg,args.case,args.out,args.dat)
            print(json.dumps({"status":r["status"],"counts":r["counts"],"elapsed_s":round(time.perf_counter()-t,4),"output":str(Path(args.out).resolve())},ensure_ascii=False,indent=2));return EXIT[r["status"]]
        if args.command=="batch":
            if args.directory and not args.case and not args.sibling_cases:raise ConfigError("目录批处理需要 --case 或 --sibling-cases")
            data=batch(args.manifest,args.directory,args.case,args.out,args.recursive,args.sibling_cases);return EXIT[data["status"]]
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print(f"ERROR: {exc}",file=sys.stderr);return 2
    return 0
