import sys,json,tempfile
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from esswave.generate import write_record
from esswave.comtrade import load
from esswave.engine import run,compact_trace

def conf(t,channels=None):return {'schema_version':1,'name':'regression probe','channels':channels or {'a':{'kind':'analog','name':'A','unit':'V'},'d':{'kind':'digital','name':'D'}},'tests':[t]}
def st():return {'id':'a','type':'analog_step','channel':'a','trigger':{'type':'time','at':.2},'window':[0,1],'baseline_window':[-.1,-.01],'target':100,'normalization':'rated','rated':100,'steady_last_s':.1,'limits':{'rise_s_max':.2}}
rows=[]
with tempfile.TemporaryDirectory() as td:
 def rec(t,y=None,d=None,**kw):return load(write_record(td,'probe',t,[{'name':'A','unit':'V','a':.01,'data':y if y is not None else [1]*len(t)}],[{'name':'D','data':d if d is not None else [0]*len(t)}],fmt='FLOAT32',**kw))
 def check(id,description,r,c,expected):
  res=run(r,c);rows.append({'id':id,'description':description,'expected':expected,'actual':res['status'],'reproduced':res['status']!=expected,'result':res})
 t=[i/1000 for i in range(1201)];y=[max(0,min(50,(x-1.14)*1000)) for x in t]
 check('B01','低阈值在触发后950ms才达到，只剩50ms录波，200ms上升时间应待复核',rec(t,y),conf(st()),'INCONCLUSIVE')
 t=[i/1000 for i in range(201)];d=[int(.098<=x<.102) for x in t]
 a={'id':'d','type':'digital_hold','channel':'d','window':[0,.1],'value':0,'debounce_s':.005}
 check('B02','窗口末端去抖尚未完成，不允许利用窗口外复位提前判通过',rec(t,d=d),conf(a),'INCONCLUSIVE')
 t=[i/1000 for i in range(201)];y=[x*100 for x in t]
 a={'id':'a','type':'analog_range','channel':'a','window':[0,.05],'min':0,'max':100,'trigger':{'type':'analog_crossing','channel':'a','threshold':10,'search':[.1,.15]}}
 check('B03','模拟触发恰位于search左边界，有前置样本证明穿越',rec(t,y),conf(a),'PASS')
 t=[i/1000 for i in range(101)]+[.2+i/1000 for i in range(101)];d=[int(x>=.2) for x in t]
 a={'id':'d','type':'digital_hold','channel':'d','window':[0,.05],'value':1,'trigger':{'type':'digital_edge','channel':'d','search':[.2,.25]}}
 check('B04','数字触发左边界的前置采样落在丢数间隙另一侧，不能当作可靠触发',rec(t,d=d),conf(a),'INCONCLUSIVE')
 # Infinite derived engineering time is currently not rejected by parser.
 t=[0,.001,.002]
 try:
  r=rec(t);p=Path(td)/'probe.cfg';lines=p.read_text().splitlines();lines[-3]='1e308';p.write_text('\n'.join(lines)+'\n')
  r=load(p);accepted=any(not __import__('math').isfinite(x) for x in r.time)
  rows.append({'id':'B05','description':'有限TIMEMULT与uint32时间戳相乘溢出，解析器应拒绝非有限时间轴','expected':'ComtradeError','actual':'accepted_nonfinite' if accepted else 'finite','reproduced':accepted})
 except ValueError as e:rows.append({'id':'B05','expected':'ComtradeError','actual':type(e).__name__,'reproduced':False})
 # Overview drops NaN separators, falsely linking disconnected samples.
 t=[i/1000 for i in range(101)];y=[1. if i<50 else (float('nan') if i<60 else 2.) for i in range(101)]
 trace=compact_trace(t,y)
 rows.append({'id':'B06','description':'缺失值被直接丢弃，导致图形跨越无效区间连线','expected':'null separator','actual':'no separators' if not any(p[1] is None for p in trace) else 'separators','reproduced':not any(p[1] is None for p in trace)})
print(json.dumps({'bugs':rows},ensure_ascii=False,indent=2))
