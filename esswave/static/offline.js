'use strict';
/* Pure local viewport: no fetch, CDN or downsampled-curve magnification.
   Full embedded samples are sliced first, then sampled for the current screen. */
function waveLower(points,x){let lo=0,hi=points.length;while(lo<hi){const m=(lo+hi)>>>1;if(points[m][0]<x)lo=m+1;else hi=m;}return lo;}
function offlineSlice(points,start,end,kind,limit){
  const lo=Math.max(0,waveLower(points,start)-1),hi=Math.min(points.length,waveLower(points,end)+1),p=points.slice(lo,hi),n=p.length;
  if(n<=limit)return {trace:p,digital_bins:[],mode:'samples',source_samples:n};
  if(kind==='digital'){
    const ids=new Set([0,n-1]);for(let i=1;i<n;i++){if(p[i][1]!==p[i-1][1]){ids.add(i-1);ids.add(i);}if(ids.size>limit)break;}
    if(ids.size<=limit)return {trace:[...ids].sort((a,b)=>a-b).map(i=>p[i]),digital_bins:[],mode:'edges',source_samples:n};
    const bins=[],size=Math.ceil(n/(limit/2));
    for(let i=0;i<n;i+=size){const part=p.slice(i,i+size);if(part.some(v=>v[1]===null))continue;
      let a=1,b=0;for(const q of part){a=Math.min(a,q[1]);b=Math.max(b,q[1]);}
      const left=Math.max(start,part[0][0]),right=Math.min(end,p[Math.min(n-1,i+size)][0]);if(right>left)bins.push([left,right,a,b]);
    }
    return {trace:[],digital_bins:bins,mode:'digital_envelope',source_samples:n};
  }
  const trace=[],size=Math.ceil(n/Math.max(1,Math.floor(limit/4)));
  for(let i=0;i<n;i+=size){const part=p.slice(i,i+size);if(part.some(v=>v[1]===null)){trace.push([part[0][0],null],[part[part.length-1][0],null]);continue;}
    let low=0,high=0;for(let j=1;j<part.length;j++){if(part[j][1]<part[low][1])low=j;if(part[j][1]>part[high][1])high=j;}
    for(const j of [...new Set([0,low,high,part.length-1])].sort((a,b)=>a-b))trace.push(part[j]);
  }
  return {trace,digital_bins:[],mode:'minmax',source_samples:n};
}
function initOfflineReport(){
  const store=JSON.parse(document.getElementById('wave-report-data').textContent);
  document.querySelectorAll('.report-wave').forEach(host=>{
    const e=store[host.dataset.waveId];if(!e)return;
    const loader=async(start,end,limit)=>{
      const raw=offlineSlice(e.raw,start,end,e.kind,limit),p=e.processed?offlineSlice(e.processed,start,end,e.kind,limit):null;
      const full=e.resolution==='full';
      return {...raw,kind:e.kind,unit:e.unit,display_points:raw.trace.length,
        start_s:start,end_s:end,gaps:e.gaps.filter(g=>g[0]<end&&g[1]>start),
        ...(p?{processed_trace:p.trace,processed_bins:p.digital_bins}:{}),
        repair_markers:(e.repairs||[]).filter(v=>v.time_s>=start&&v.time_s<=end),
        warning:(full?'离线内嵌完整证据窗口采样；放大后读取局部采样':'本图仅内嵌证据摘要，放大不能恢复未保存采样；请在工作台打开原始录波')+'；'+(raw.mode==='digital_envelope'?'变位密集包络，请继续放大':raw.mode==='minmax'?'当前显示保峰抽稀':'当前显示窗口内保存的采样')+'；查看不改变测试结论'};
    };
    new WaveTimeline(host,{start:e.bounds[0],end:e.bounds[1],target:e.target??null,band:e.band||0,trigger:e.trigger,compare:!!e.processed,parameters:{},loader});
  });
}
initOfflineReport();
