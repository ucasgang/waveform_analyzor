'use strict';
let plotSerial=0;
function plot(trace,kind='analog',unit='',target=null,band=0,trigger=null,options={}) {
  trace=trace||[];const bins=options.digitalBins||[],domain=options.domain;
  if(!trace.length&&!bins.length)return '<div class="empty">当前窗口没有可显示的采样。</div>';
  const W=1000,H=245,L=62,R=17,T=23,B=38,id='waveclip'+(++plotSerial);
  const xmin=domain?domain[0]:trace[0][0],xmax=domain?domain[1]:trace[trace.length-1][0];
  let ymin=Infinity,ymax=-Infinity;
  [...trace,...(options.overlayTrace||[])].forEach(p=>{if(p[1]!==null&&Number.isFinite(p[1])&&p[0]>=xmin-(xmax-xmin)*.01&&p[0]<=xmax+(xmax-xmin)*.01){ymin=Math.min(ymin,p[1]);ymax=Math.max(ymax,p[1]);}});
  if(kind==='digital'){ymin=-.15;ymax=1.15;}else{
    if(!Number.isFinite(ymin)){const valid=trace.filter(p=>p[1]!==null&&Number.isFinite(p[1]));if(valid.length){ymin=Math.min(valid[0][1],valid[valid.length-1][1]);ymax=Math.max(valid[0][1],valid[valid.length-1][1]);}else{ymin=0;ymax=1;}}
    if(target!==null){ymin=Math.min(ymin,target-band);ymax=Math.max(ymax,target+band);}const pad=(ymax-ymin)*.12||1;ymin-=pad;ymax+=pad;
  }
  if(options.yDomain){[ymin,ymax]=options.yDomain;}
  const x=v=>L+(v-xmin)/(xmax-xmin||1)*(W-L-R),y=v=>T+(ymax-v)/(ymax-ymin||1)*(H-T-B);
  let s=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${E(unit)}波形"><defs><clipPath id="${id}"><rect x="${L}" y="${T}" width="${W-L-R}" height="${H-T-B}"/></clipPath></defs><rect width="100%" height="100%" rx="9" fill="#f8fafc"/>`;
  for(let j=0;j<5;j++){const Y=T+j*(H-T-B)/4,V=ymax-j*(ymax-ymin)/4;s+=`<path d="M${L} ${Y}H${W-R}" stroke="#e1e9f0"/><text x="${L-8}" y="${Y+4}" fill="#60758b" font-size="11" text-anchor="end">${E(Number(V.toPrecision(5)))}</text>`;}
  const precision=Math.max(0,Math.min(10,Math.ceil(-Math.log10((xmax-xmin)/5))+1));
  for(let j=0;j<6;j++){const X=L+j*(W-L-R)/5,V=xmin+j*(xmax-xmin)/5;s+=`<text x="${X}" y="${H-12}" fill="#60758b" font-size="11" text-anchor="middle">${E(V.toFixed(precision))}s</text>`;}
  s+=`<g clip-path="url(#${id})">`;
  if(target!==null)s+=`<rect x="${L}" y="${y(target+band)}" width="${W-L-R}" height="${Math.max(0,y(target-band)-y(target+band))}" fill="#e1f2e9"/><path d="M${L} ${y(target)}H${W-R}" stroke="#319980" stroke-dasharray="5 5"/>`;
  let d='',pen=false;trace.forEach(p=>{if(p[1]===null||!Number.isFinite(p[1])){pen=false;return;}d+=pen?(kind==='digital'?`H${x(p[0]).toFixed(3)}V${y(p[1]).toFixed(3)}`:`L${x(p[0]).toFixed(3)},${y(p[1]).toFixed(3)}`):`M${x(p[0]).toFixed(3)},${y(p[1]).toFixed(3)}`;pen=true;});
  if(options.showRaw!==false)s+=`<path class="wave-data" d="${d}" fill="none" stroke="#4a78bf" stroke-width="1.7"/>`;
  for(const b of (options.showRaw===false?[]:bins)){if(b[2]===b[3])s+=`<path d="M${x(b[0])} ${y(b[2])}H${x(b[1])}" stroke="#4a78bf" stroke-width="2"/>`;else s+=`<rect class="digital-density" x="${x(b[0])}" y="${y(1)}" width="${Math.max(.5,x(b[1])-x(b[0]))}" height="${y(0)-y(1)}" fill="#4a78bf" opacity=".28"/>`;}
  if(options.overlayTrace?.length && options.showProcessed!==false){
    let path='',seen=false;for(const p of options.overlayTrace){if(p[1]===null||!Number.isFinite(p[1])){seen=false;continue;}path+=seen?(kind==='digital'?`H${x(p[0]).toFixed(3)}V${y(p[1]).toFixed(3)}`:`L${x(p[0]).toFixed(3)},${y(p[1]).toFixed(3)}`):`M${x(p[0]).toFixed(3)},${y(p[1]).toFixed(3)}`;seen=true;}
    s+=`<path class="wave-processed" d="${path}" fill="none" stroke="#ca6b16" stroke-width="2"/>`;
  }
  if(options.showProcessed!==false)for(const b of options.processedBins||[]){if(b[2]===b[3])s+=`<path d="M${x(b[0])} ${y(b[2])}H${x(b[1])}" stroke="#ca6b16" stroke-width="2"/>`;else s+=`<rect x="${x(b[0])}" y="${y(1)}" width="${Math.max(.5,x(b[1])-x(b[0]))}" height="${y(0)-y(1)}" fill="#ca6b16" opacity=".2"/>`;}
  if(options.showRepairs!==false){
    for(const p of options.repairMarkers||[])s+=`<circle class="repair-marker" cx="${x(p.time_s)}" cy="${y(p.raw)}" r="3.5" fill="none" stroke="#c14c62" stroke-width="1.6"><title>${E(p.time_s)}s：原始 ${E(p.raw)} → 替换 ${E(p.replacement)}（待核实候选）</title></circle>`;
    for(const p of options.rejectedSpans||[])s+=`<rect class="rejected-span" x="${x(p.start_s)}" y="${T}" width="${Math.max(1,x(p.end_s)-x(p.start_s))}" height="${H-T-B}" fill="#c14c62" opacity=".12"><title>被显式去抖抑制的原始脉冲</title></rect>`;
  }
  for(const g of options.gaps||[])s+=`<rect class="sampling-gap" x="${x(g[0])}" y="${T}" width="${Math.max(1,x(g[1])-x(g[0]))}" height="${H-T-B}" fill="#8996a7" opacity=".25"/>`;
  if(trigger!==null&&trigger>=xmin&&trigger<=xmax)s+=`<path d="M${x(trigger)} ${T}V${H-B}" stroke="#b28b42" stroke-dasharray="4 5"/>`;
  s+=`</g><text x="${L}" y="14" fill="#61758a" font-size="11">${E(unit||'逻辑值')} · 录波起点相对时间${bins.length?' · 变位密度包络':''}</text></svg>`;
  return s;
}
