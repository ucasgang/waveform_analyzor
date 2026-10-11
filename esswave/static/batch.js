'use strict';
let batchFiles=new Map(),batchState=null,batchUploading=false,batchTimer=null,batchPage=0;
const batchLabels={...labels,WAITING:'待上传',READY:'待执行',RUNNING:'执行中',CANCELLED:'已取消 / 未执行'};
function batchControls(){const active=batchState&&['STAGING','RUNNING'].includes(batchState.state);
  $('batchScan').disabled=!!active||batchUploading||busy;
  $('batchStart').disabled=!batchState||batchState.state!=='STAGING'||batchUploading||busy;
  $('batchCancel').disabled=!active||busy;
  $('batchExport').disabled=!batchState||!['COMPLETE','CANCELLED'].includes(batchState.state)||busy;
}
window.refreshBatchControls=batchControls;
function batchRender(){
  batchControls();if(!batchState)return;
  const b=batchState;$('batchProgress').max=b.total;$('batchProgress').value=b.done;
  $('batchStatus').textContent=`${{STAGING:'扫描完成 / 上传准备',RUNNING:'执行中',COMPLETE:'执行完成',CANCELLED:'已取消'}[b.state]} · ${b.done}/${b.total} 组已结束 · 验收汇总：${batchLabels[b.verdict]}`;
  $('batchCounts').innerHTML=['PASS','FAIL','INCONCLUSIVE','ERROR','CANCELLED'].map(s=>`<span class="batch-count ${s}">${E(batchLabels[s])} <b>${b.counts[s]}</b></span>`).join('');
  const f=$('batchFilter').value,q=$('batchSearch').value.toLowerCase();const all=b.rows.filter(r=>(f==='all'||r.status===f)&&(r.path+' '+r.reason).toLowerCase().includes(q));
  batchPage=Math.min(batchPage,Math.max(0,Math.ceil(all.length/100)-1));const rows=all.slice(batchPage*100,(batchPage+1)*100);
  $('batchRows').innerHTML=rows.map(r=>`<tr><td>${E(r.id)}</td><td class="batch-path">${E(r.path)}<small>${b.mode==='common'?'统一用例快照':E(r.case||'无匹配用例')}</small></td><td><span class="status ${E(r.status)}">${E(batchLabels[r.status])}</span></td><td>${E(r.reason)}${r.counts?`<small>${r.counts.PASS}通过 / ${r.counts.FAIL}失败 / ${r.counts.INCONCLUSIVE}待复核 / ${r.counts.ERROR}错误</small>`:''}</td><td>${r.has_result?`<button class="textbutton" data-batch-open="${E(r.id)}">打开结果与可缩放波形 →</button>`:'—'}</td></tr>`).join('')||'<tr><td colspan="5">无匹配记录。</td></tr>';
  $('batchPagination').textContent=`${all.length} 组匹配记录 · 第 ${batchPage+1} / ${Math.max(1,Math.ceil(all.length/100))} 页，每页最多100组`;
  $('batchPrev').disabled=batchPage===0;$('batchNext').disabled=(batchPage+1)*100>=all.length;
}
function selectBatchFiles(input){batchFiles=new Map();for(const file of input.files){const path=file.webkitRelativePath||file.name;if(batchFiles.has(path)){toast('存在重复路径，请重新选择文件夹','error');return;}batchFiles.set(path,file);}
  $('batchSelection').textContent=`已选择 ${batchFiles.size} 个文件 · ${(Array.from(batchFiles.values()).reduce((s,f)=>s+f.size,0)/1024**2).toFixed(2)} MiB。点击扫描检查配对。`;
  batchControls();
}
$('batchFolder').addEventListener('change',e=>selectBatchFiles(e.target));$('batchMulti').addEventListener('change',e=>selectBatchFiles(e.target));
$('batchScan').onclick=()=>task('正在检查文件夹配对与用例模式…',async()=>{
  let files=[...batchFiles].map(([path,file])=>({path,size:file.size}));if(!files.length)throw Error('请先选择文件夹或多选文件。');
  if(!$('batchRecursive').checked){const hasRoot=files.some(f=>f.path.includes('/'));files=files.filter(f=>f.path.split('/').length===(hasRoot?2:1));}
  const mode=$('batchMode').value;batchState=await api('/api/batch/create',{files,mode,...(mode==='common'?{config:editor()}:{})});batchPage=0;batchRender();
  toast(`扫描到 ${batchState.total} 组；${batchState.counts.ERROR} 组配对错误。检查清单后开始批量执行。`);
});
async function batchPoll(){clearTimeout(batchTimer);if(!batchState)return;
  try{batchState=await api('/api/batch/status/'+batchState.id);batchRender();
    if(batchState.state==='RUNNING')batchTimer=setTimeout(batchPoll,500);
  }catch(e){toast('批次状态读取失败：'+e.message+'。源文件不会因此判为通过。','error',true);batchTimer=setTimeout(batchPoll,1800);}
}
$('batchStart').onclick=async()=>{
  if(!batchState||batchUploading)return;
  const missing=batchState.rows.filter(r=>r.status==='WAITING').some(r=>[r.cfg,r.dat,r.case].filter(Boolean).some(p=>!batchFiles.has(p)));
  if(missing){toast('请重新选择本批次原文件夹，或取消后重新扫描。未上传文件仍保留为待上传，不会跳过。','error',true);return;}
  batchUploading=true;batchControls();const ident=batchState.id;
  try{
    for(const row of batchState.rows.filter(r=>r.status==='WAITING')){
      if(batchState.state!=='STAGING')break;
      const cfg=batchFiles.get(row.cfg),dat=batchFiles.get(row.dat),caseFile=row.case?batchFiles.get(row.case):null;
      const bytes=new ArrayBuffer(8),v=new DataView(bytes);v.setUint32(0,cfg.size,true);v.setUint32(4,caseFile?.size||0,true);
      $('batchStatus').textContent=`正在上传 ${row.path} 至本机服务；每次只上传一组`;
      try{batchState=await api(`/api/batch/upload/${ident}/${row.id}`,new Blob([bytes,cfg,dat,...(caseFile?[caseFile]:[])]),true);}
      catch(e){
        batchState=await api('/api/batch/status/'+ident);
        const current=batchState.rows.find(r=>r.id===row.id);
        if(batchState.state==='STAGING'&&current.status==='WAITING')batchState=await api(`/api/batch/reject/${ident}/${row.id}`,{reason:'上传失败：'+e.message});
      }
      batchRender();
    }
    if(batchState.state==='STAGING'){batchState=await api('/api/batch/start/'+ident,{});batchRender();await batchPoll();}
  }catch(e){toast('批处理尚未完成：'+e.message,'error',true);}
  finally{batchUploading=false;batchControls();}
};
$('batchCancel').onclick=async()=>{if(!batchState)return;try{batchState=await api('/api/batch/cancel/'+batchState.id,{});batchRender();if(batchState.state==='RUNNING')await batchPoll();toast('已请求取消；正在计算的这一组结束后停止，其余组标记为未执行。','info');}catch(e){toast(e.message,'error',true);}};
$('batchExport').onclick=()=>task('正在打包全部已完成报告和汇总…',async()=>{
  const r=await fetch('/api/batch/export/'+batchState.id,{headers:{'X-ESS-Token':window.ESS_TOKEN}});if(!r.ok)throw Error((await r.json()).error);
  saveBlob(await r.blob(),'ESS_batch_reports_'+batchState.id+'.zip');toast('报告包已导出。解压后打开 index.html；每组报告均有缩放控件。');
});
$('batchRows').onclick=e=>{const b=e.target.closest('[data-batch-open]');if(!b)return;
  task('正在打开该组原始录波与已保存结果…',async()=>{
    const data=await api(`/api/batch/open/${batchState.id}/${b.dataset.batchOpen}`,{});
    loadSummary(data.record);setConfig(data.config);result=data.result;renderResults();tab('results');toast('已打开批次中的这一组；缩放不改变用例或结论。');
  });
};
for(const id of ['batchFilter','batchSearch'])$(id).addEventListener(id==='batchSearch'?'input':'change',()=>{batchPage=0;batchRender();});
$('batchPrev').onclick=()=>{batchPage--;batchRender();};$('batchNext').onclick=()=>{batchPage++;batchRender();};
api('/api/batch/current').then(b=>{if(b.id){batchState=b;batchRender();if(b.state==='RUNNING')batchPoll();}else batchControls();}).catch(()=>batchControls());
