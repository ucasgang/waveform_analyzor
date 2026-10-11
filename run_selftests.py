"""Run all shipped tests with no external dependency; emit a reproducible summary."""
from datetime import datetime,timezone
import json
from pathlib import Path
import platform
import sys
import time
import unittest
from esswave.report import atomic_text
from esswave import __version__

class CapturingResult(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self.outcomes=[]
    def addSuccess(self,test):super().addSuccess(test);self.outcomes.append({'test':test.id(),'status':'PASS'})
    def addFailure(self,test,err):super().addFailure(test,err);self.outcomes.append({'test':test.id(),'status':'FAIL'})
    def addError(self,test,err):super().addError(test,err);self.outcomes.append({'test':test.id(),'status':'ERROR'})
    def addSkip(self,test,reason):super().addSkip(test,reason);self.outcomes.append({'test':test.id(),'status':'SKIP','reason':reason})

def main():
    root=Path(__file__).resolve().parent
    suite=unittest.defaultTestLoader.discover(str(root/'tests'),pattern='test_*.py')
    t=time.perf_counter();result=unittest.TextTestRunner(verbosity=2,resultclass=CapturingResult).run(suite)
    data={'version':__version__,'generated_utc':datetime.now(timezone.utc).isoformat(),'python':sys.version,'platform':platform.platform(),
          'test_count':result.testsRun,'passed':result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped),
          'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),
          'elapsed_s':time.perf_counter()-t,'successful':result.wasSuccessful(),'outcomes':result.outcomes,
          'scope_note':'合成、解析、算法、接口与回归检查；不构成所有厂家设备或现场工况的证明。'}
    atomic_text(root/'reports/selftest_results.json',json.dumps(data,ensure_ascii=False,indent=2))
    return 0 if result.wasSuccessful() else 1
if __name__=='__main__':raise SystemExit(main())
