"""Generate 128 analog + 384 digital channels; benchmark in a fresh process."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from esswave.comtrade import load
from esswave.engine import run
from esswave.generate import scenario
from esswave.report import atomic_text

def worker(cfg,case):
    t=time.perf_counter();rec=load(cfg);parse=time.perf_counter()-t
    config=json.loads(Path(case).read_text(encoding='utf-8'));t=time.perf_counter();result=run(rec,config);analysis=time.perf_counter()-t
    try:
        import resource
        rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_mb=rss/(1024**2 if sys.platform=='darwin' else 1024)
    except ImportError:peak_mb=None
    preexec_peak=peak_mb
    current_mb=None
    if sys.platform.startswith('linux'):
        status={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in Path('/proc/self/status').read_text().splitlines() if ':' in line}
        peak_mb=float(status['VmHWM'].split()[0])/1024
        current_mb=float(status['VmRSS'].split()[0])/1024
    return {'platform':platform.platform(),'python':sys.version,'processor':platform.processor(),'samples':len(rec.time),
            'analog_channels':rec.meta['analog_count'],'digital_channels':rec.meta['digital_count'],
            'total_channel_values':len(rec.time)*len(rec.channels),'dat_bytes':Path(cfg).with_suffix('.dat').stat().st_size,
            'parse_s':parse,'assertions_s':analysis,'process_peak_rss_mib':peak_mb,'current_rss_mib':current_mb,'ru_maxrss_mib_including_preexec':preexec_peak,'status':result['status'],
            'assertion_counts':result['counts'],'measurement_note':'Fresh-process single run; includes reading/scaling all channels; assertion timing covers the six demo assertions, not 512 assertions. Peak RSS is whole process, not incremental allocation. On Linux VmHWM is used to exclude inherited pre-exec high-water history; ru_maxrss is retained separately. No hardware throughput guarantee.'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--samples',type=int,default=20001);p.add_argument('--out',default=str(ROOT/'reports/benchmark.json'));p.add_argument('--worker',nargs=2)
    args=p.parse_args()
    if args.worker:print(json.dumps(worker(*args.worker),ensure_ascii=False));return
    if args.samples<2001:p.error('--samples must be >= 2001')
    with tempfile.TemporaryDirectory() as folder:
        cfg,case=scenario(folder,'benchmark_512',extra_analog=125,extra_digital=380,duration=(args.samples-1)*.001)
        child=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--worker',str(cfg),str(case)],capture_output=True,text=True,check=True)
        data=json.loads(child.stdout)
    atomic_text(Path(args.out),json.dumps(data,ensure_ascii=False,indent=2));print(json.dumps(data,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
