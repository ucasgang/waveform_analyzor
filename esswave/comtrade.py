"""Strict COMTRADE CFG/DAT reader. Standard library only; no silent repair.

Supported subset: 1991, 1999, 2001, 2013; ASCII/BINARY/BINARY32/FLOAT32.
CFF, vendor extensions and phasor schemas are deliberately not inferred.
Analog values are engineering values a*x+b in the side declared by CFG.
"""
from __future__ import annotations
from array import array
from dataclasses import dataclass, asdict
from datetime import datetime
from decimal import Decimal
import csv
import hashlib
import io
import math
from pathlib import Path
import struct
from typing import Any

class ComtradeError(ValueError):
    """Invalid, unsupported or resource-excessive input."""

@dataclass(frozen=True)
class Channel:
    key: str
    index: int
    kind: str
    name: str
    unit: str = ""
    phase: str = ""
    circuit: str = ""
    a: float = 1.0
    b: float = 0.0
    skew_us: float = 0.0
    primary: float = 1.0
    secondary: float = 1.0
    side: str = "P"
    normal: int = 0
    raw_min: float = 0.0
    raw_max: float = 0.0

@dataclass
class Record:
    meta: dict[str, Any]
    channels: list[Channel]
    time: array
    values: dict[str, array]
    warnings: list[str]
    gaps: list[tuple[float, float]]
    hashes: dict[str, str]

    def summary(self) -> dict[str, Any]:
        return {**self.meta, "samples": len(self.time), "start_s": self.time[0],
                "end_s": self.time[-1], "channels": [asdict(c) for c in self.channels],
                "warnings": self.warnings, "gap_count": len(self.gaps), "hashes": self.hashes}


def finite(s: Any, label: str) -> float:
    try:
        v = float(s)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ComtradeError(f"{label}: 不是有效数值") from exc
    if not math.isfinite(v):
        raise ComtradeError(f"{label}: 必须为有限数值")
    return v


def decode_cfg(raw: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ComtradeError("CFG 编码无法识别；请转换为 UTF-8 或 GB18030")


def stamp(row: list[str], revision: int) -> Decimal:
    if len(row) != 2:
        raise ComtradeError("CFG 日期时间必须为 日期,时间 两列")
    fmt = "%m/%d/%Y" if revision == 1991 else "%d/%m/%Y"
    try:
        date = datetime.strptime(row[0], fmt)
        h, m, sec = row[1].split(":")
        h, m, sec = int(h), int(m), Decimal(sec)
        if not (0 <= h < 24 and 0 <= m < 60 and 0 <= sec < 60):
            raise ValueError("time outside range")
        return Decimal(date.toordinal() * 86400 + h * 3600 + m * 60) + sec
    except Exception as exc:
        raise ComtradeError("无效 CFG 日期时间；1991 使用月/日/年，后续版本使用日/月/年；不支持闰秒") from exc


def parse_cfg(raw: bytes) -> tuple[dict, list[Channel], list[str]]:
    if len(raw) > 8 * 1024 * 1024:
        raise ComtradeError("CFG 超过 8 MiB 安全上限")
    text, encoding = decode_cfg(raw)
    rows = [[c.strip() for c in row] for row in csv.reader(io.StringIO(text)) if row]
    at = 0
    def take() -> list[str]:
        nonlocal at
        if at >= len(rows):
            raise ComtradeError(f"CFG 提前结束，缺少第 {at+1} 行")
        row = rows[at]; at += 1
        return row
    try:
        first = take()
        if len(first) not in (2, 3):
            raise ComtradeError("CFG 首行必须包含站名、设备名及可选版本号")
        rev = int(first[2]) if len(first) == 3 else 1991
        if rev not in (1991, 1999, 2001, 2013):
            raise ComtradeError(f"暂不支持 COMTRADE 版本 {rev}")
        counts = take()
        if len(counts) != 3 or not counts[1].upper().endswith("A") or not counts[2].upper().endswith("D"):
            raise ComtradeError("CFG 通道数量行必须为 总数,nA,nD")
        total, na, nd = int(counts[0]), int(counts[1][:-1]), int(counts[2][:-1])
        if na < 0 or nd < 0 or total != na + nd or not 1 <= total <= 4096:
            raise ComtradeError("CFG 通道数量不一致或不在 1..4096 范围")
        channels = []
        for k in range(na):
            r = take()
            need = 10 if rev == 1991 else 13
            if len(r) != need:
                raise ComtradeError(f"模拟通道 A{k+1} 需要 {need} 列，实际 {len(r)} 列")
            if int(r[0]) != k+1 or not r[1]:
                raise ComtradeError("模拟通道序号必须从 1 连续排列，名称不能为空")
            a,b,sk,lo,hi = [finite(r[i], f"A{k+1} 系数") for i in range(5,10)]
            pri,sec,side = (1.,1.,"P") if rev == 1991 else (finite(r[10],"primary"),finite(r[11],"secondary"),r[12].upper())
            if a == 0 or lo > hi or pri <= 0 or sec <= 0 or side not in ("P","S"):
                raise ComtradeError(f"A{k+1} 缩放、范围或变比配置无效")
            channels.append(Channel(f"A{k+1}",k+1,"analog",r[1],r[4],r[2],r[3],a,b,sk,pri,sec,side,0,lo,hi))
        for k in range(nd):
            r = take()
            need = 3 if rev == 1991 else 5
            if len(r) != need:
                raise ComtradeError(f"数字通道 D{k+1} 需要 {need} 列")
            normal = int(r[2] if rev == 1991 else r[4])
            if int(r[0]) != k+1 or not r[1] or normal not in (0,1):
                raise ComtradeError("数字通道序号、名称或正常状态无效")
            channels.append(Channel(f"D{k+1}",k+1,"digital",r[1],phase="" if rev==1991 else r[2], circuit="" if rev==1991 else r[3],normal=normal))
        freq = finite(take()[0],"额定频率")
        if freq <= 0: raise ComtradeError("额定频率必须大于 0")
        nrates = int(take()[0]); rates = []
        if nrates < 0 or nrates > 1000: raise ComtradeError("采样率段数无效")
        for _ in range(nrates):
            r = take()
            rate, end = finite(r[0],"采样率"), int(r[1])
            if rate <= 0 or end <= (rates[-1][1] if rates else 0):
                raise ComtradeError("采样率必须为正，结束采样序号必须递增")
            rates.append((rate,end))
        start_row, trig_row = take(), take()
        trigger = float(stamp(trig_row,rev)-stamp(start_row,rev))
        fmt = take()[0].upper()
        if fmt not in ("ASCII","BINARY","BINARY32","FLOAT32"):
            raise ComtradeError(f"暂不支持 DAT 格式 {fmt}")
        if rev != 2013 and fmt in ("BINARY32","FLOAT32"):
            raise ComtradeError("BINARY32/FLOAT32 需要 2013 CFG")
        mult = 1. if rev==1991 else finite(take()[0],"TIMEMULT")
        if mult <= 0: raise ComtradeError("TIMEMULT 必须大于 0")
        warnings = []
        extra = rows[at:]
        if rev != 2013 and extra:
            raise ComtradeError("CFG 存在未支持的尾部扩展；禁止静默忽略")
        if rev == 2013 and len(extra) != 2:
            raise ComtradeError("2013 CFG 需要 time_code/local_code 与 tmq_code/leapsec 两个尾行")
        if rev == 2013 and any(len(x)!=2 for x in extra):
            raise ComtradeError("2013 时间质量尾行必须各有两列")
        if rev == 2013 and extra[1][0] not in ("0","00"):
            warnings.append("2013 时间质量代码不是 0；绝对时间同步精度未获验证")
        return {"station":first[0],"device":first[1],"revision":rev,"format":fmt,
                "analog_count":na,"digital_count":nd,"frequency_hz":freq,
                "sample_rates":rates,"trigger_s":trigger,"timemult":mult,
                "start_timestamp":",".join(start_row),"trigger_timestamp":",".join(trig_row),
                "encoding":encoding,"time_metadata":extra}, channels, warnings
    except (ValueError,IndexError,TypeError) as exc:
        if isinstance(exc,ComtradeError): raise
        raise ComtradeError(f"CFG 第 {at} 行格式错误: {exc}") from exc


def load(cfg_path: str | Path, dat_path: str | Path | None = None,
         max_memory_mb: int = 512) -> Record:
    cfg = Path(cfg_path)
    if cfg.suffix.lower() != ".cfg":
        raise ComtradeError("仅支持 CFG + DAT 文件对；CFF 请先用设备工具导出为 CFG/DAT")
    if dat_path is None:
        candidates = [p for p in cfg.parent.iterdir() if p.is_file() and p.stem.lower()==cfg.stem.lower() and p.suffix.lower()==".dat"]
        if len(candidates) != 1: raise ComtradeError("未找到唯一的同名 DAT 文件")
        dat_path = candidates[0]
    dat = Path(dat_path)
    if cfg.stat().st_size > 8*1024**2 or dat.stat().st_size > 512*1024**2:
        raise ComtradeError("文件超过上限（CFG 8 MiB / DAT 512 MiB）；请拆分记录")
    raw_cfg = cfg.read_bytes()
    meta, channels, warnings = parse_cfg(raw_cfg)
    meta.update({"cfg_file":cfg.name,"dat_file":dat.name})
    na,nd,fmt,rev = meta["analog_count"],meta["digital_count"],meta["format"],meta["revision"]
    values = {c.key:array("d" if c.kind=="analog" else "B") for c in channels}
    arrays = [values[c.key] for c in channels]
    times = array("d"); time_raw = array("d")
    mem_per_sample = 24 + 8*na + nd
    sample_limit = max_memory_mb*1024**2 // max(mem_per_sample,1)
    missing = {"ASCII":99999., "BINARY":-1. if rev==1991 else -32768.,
               "BINARY32":-2147483648.,"FLOAT32":-3.4028234663852886e38}[fmt]
    count=0; has_missing_time=False
    dat_hash=hashlib.sha256()
    def append(row, binary=False):
        nonlocal count,has_missing_time
        count+=1
        if count>sample_limit: raise ComtradeError(f"解码后的数据超过 {max_memory_mb} MiB 内存预算")
        if int(row[0])!=count or float(row[0])!=count:
            raise ComtradeError(f"第 {count} 个采样序号不连续；不进行补点或自动修复")
        tv=finite(row[1],f"采样 {count} 时间戳")
        if tv < 0 or tv>4294967295 or tv != int(tv):
            raise ComtradeError(f"采样 {count} 时间戳须为 uint32 整数")
        if tv==4294967295: has_missing_time=True
        time_raw.append(tv)
        for i in range(na):
            x=row[2+i]
            if x=="" and rev==1991: v=float("nan")
            else:
                try: v=float(x)
                except (ValueError,TypeError) as exc: raise ComtradeError(f"采样 {count} A{i+1} 非法数值") from exc
                if v==missing: v=float("nan")
                elif not math.isfinite(v) and not (fmt=="FLOAT32" and math.isnan(v)):
                    raise ComtradeError(f"采样 {count} A{i+1} 不允许非有限数值")
            scaled=v*channels[i].a+channels[i].b
            if math.isfinite(v) and not math.isfinite(scaled):
                raise ComtradeError(f"采样 {count} A{i+1} 工程值缩放溢出")
            arrays[i].append(scaled)
        for i in range(nd):
            v=((row[2+na+i//16] >> (i%16)) & 1) if binary else row[2+na+i]
            if not binary and str(v).strip() not in ("0","1"):
                raise ComtradeError(f"采样 {count} D{i+1} 必须为 0 或 1")
            arrays[na+i].append(int(v))
    try:
        if fmt=="ASCII":
            with dat.open("rb") as f:
                for line_num, line in enumerate(f,1):
                    dat_hash.update(line)
                    if not line.strip(): raise ComtradeError(f"DAT 第 {line_num} 行为空")
                    if line_num==1: line=line.removeprefix(b"\xef\xbb\xbf")
                    row=next(csv.reader([line.decode("ascii").strip()]))
                    if len(row)!=2+na+nd: raise ComtradeError(f"DAT 第 {line_num} 行列数错误")
                    append(row)
        else:
            code={"BINARY":"h","BINARY32":"i","FLOAT32":"f"}[fmt]
            unpacker=struct.Struct("<II"+code*na+"H"*((nd+15)//16))
            if dat.stat().st_size % unpacker.size:
                raise ComtradeError("DAT 二进制记录被截断或通道数量与 CFG 不符")
            with dat.open("rb") as f:
                while True:
                    block=f.read(unpacker.size*1024)
                    if not block: break
                    dat_hash.update(block)
                    for row in unpacker.iter_unpack(block): append(row,True)
    except (UnicodeError, ValueError,struct.error,OverflowError) as exc:
        if isinstance(exc,ComtradeError): raise
        raise ComtradeError(f"DAT 解码失败，采样 {count}: {exc}") from exc
    if count<2: raise ComtradeError("至少需要 2 个采样点")
    rates=meta["sample_rates"]
    if rates and count!=rates[-1][1]: raise ComtradeError(f"CFG 声明 {rates[-1][1]} 点，DAT 实际 {count} 点；记录不完整")
    all_zero=all(v==0 for v in time_raw)
    all_missing=all(v==4294967295 for v in time_raw)
    if has_missing_time and not all_missing:
        raise ComtradeError("时间戳仅部分缺失；不允许静默重建")
    reconstruct=all_zero or all_missing
    if reconstruct and not rates:
        raise ComtradeError("DAT 时间戳不可用且 CFG 无采样率，无法建立时间轴")
    segment=0; t=0.; gaps=[]; max_dt=0.
    for i in range(count):
        if rates:
            while i+1>rates[segment][1]: segment+=1
            expected_dt=1./rates[segment][0]
        else: expected_dt=None
        if reconstruct:
            if i: t+=expected_dt
        else: t=time_raw[i]*meta["timemult"]*1e-6
        if not math.isfinite(t): raise ComtradeError(f"采样 {i+1} 时间轴换算溢出")
        if i:
            dt=t-times[-1]
            if dt<=0: raise ComtradeError(f"采样 {i+1} 时间戳未严格递增")
            max_dt=max(max_dt,dt)
            if expected_dt and dt > expected_dt*1.5+meta["timemult"]*1e-6:
                gaps.append((times[-1],t))
        times.append(t)
    if not rates:
        ds=sorted(times[i]-times[i-1] for i in range(1,count)); typical=ds[len(ds)//2]
        gaps=[(times[i-1],times[i]) for i in range(1,count) if times[i]-times[i-1]>typical*1.5]
        warnings.append("CFG 未提供采样率：按 DAT 时间戳分析，间隙检测以中位采样间隔估计")
    if reconstruct: warnings.append("DAT 时间戳全部为零或缺失：已按 CFG 分段采样率重建时间轴")
    if gaps: warnings.append(f"发现 {len(gaps)} 个疑似采样间隙；涉及的分析窗口不得判为通过")
    for c in channels:
        if c.kind=="analog" and any(not math.isfinite(v) for v in values[c.key]):
            warnings.append(f"{c.key} 含缺失值；涉及缺失值的窗口将标记数据不足")
    meta.update({"time_source":"sample_rates" if reconstruct else "dat_timestamps", "max_sample_interval_s":max_dt})
    return Record(meta,channels,times,values,warnings,gaps,
                  {"cfg_sha256":hashlib.sha256(raw_cfg).hexdigest(),"dat_sha256":dat_hash.hexdigest()})
