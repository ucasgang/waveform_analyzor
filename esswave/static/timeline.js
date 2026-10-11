'use strict';
/* A viewport is a display-only operation. Each fetch re-reads its original
   samples; sequence numbers and abort prevent stale channel/range responses. */
class WaveTimeline {
  static instances = new Set();
  constructor(host, options) {
    this.host=host;this.options=options;this.full=[options.start,options.end];
    this.range=this.full.slice();this.data=null;this.overview=null;this.serial=0;
    this.layers={raw:true,processed:true,repairs:true};this.yRange=null;
    this.dead=false;this.drag=null;this.timer=null;this.controller=null;
    WaveTimeline.instances.add(this);
    host.classList.add('timeline');
    host.innerHTML=`<div class="timeline-tools"><div class="timeline-buttons">
      <button type="button" data-action="in" title="以视窗中心放大">＋ 放大</button>
      <button type="button" data-action="out" title="以视窗中心缩小">－ 缩小</button>
      <button type="button" data-action="left" title="向前平移四分之一视窗">←</button>
      <button type="button" data-action="right" title="向后平移四分之一视窗">→</button>
      <button type="button" data-action="reset">全览 / 复位</button>
      <button type="button" data-action="yin" title="仅放大幅值轴">幅值 ＋</button>
      <button type="button" data-action="yout" title="仅缩小幅值轴">幅值 －</button>
      <button type="button" data-action="yreset">幅值复位</button></div>
      <form class="timeline-range"><label>开始 <input data-time="start" type="number" step="any" aria-label="视窗开始秒"></label>
      <label>结束 <input data-time="end" type="number" step="any" aria-label="视窗结束秒"></label><span>s</span>
      <button type="submit">定位</button></form></div>
      ${options.compare?`<div class="timeline-layers"><label><input type="checkbox" data-layer="raw" checked> 原始曲线</label><label><input type="checkbox" data-layer="processed" checked> 处理曲线</label><label><input type="checkbox" data-layer="repairs" checked> 修复 / 抑制候选</label><span>蓝：原始　橙：处理　○：候选点</span></div>`:''}
      <div class="timeline-gesture">滚轮缩放 · 拖动平移 · Shift + 拖动框选 · 双击复位 <span class="timeline-span"></span></div>
      <div class="timeline-frame" tabindex="0" role="group" aria-label="可缩放波形时间轴：加减键缩放，左右键平移，Home复位">
        <div class="timeline-svg"></div><div class="timeline-selection" hidden></div><div class="timeline-cursor" hidden></div>
      </div><div class="timeline-nav" title="点击概览定位；拖动蓝色视窗平移" role="group" aria-label="全录波时间概览">
        <div class="timeline-nav-bg"></div><div class="timeline-nav-window"></div></div>
      <div class="timeline-nav-labels"><span></span><span></span></div>
      <div class="timeline-status" role="status">正在读取波形…</div>`;
    host.querySelectorAll('[data-layer]').forEach(el=>el.addEventListener('change',()=>{this.layers[el.dataset.layer]=el.checked;this.draw();}));
    this.frame=host.querySelector('.timeline-frame');this.svg=host.querySelector('.timeline-svg');
    this.status=host.querySelector('.timeline-status');this.selection=host.querySelector('.timeline-selection');
    this.cursor=host.querySelector('.timeline-cursor');this.nav=host.querySelector('.timeline-nav');
    host.querySelector('.timeline-nav-labels span:first-child').textContent=this.format(this.full[0])+' s';
    host.querySelector('.timeline-nav-labels span:last-child').textContent=this.format(this.full[1])+' s';
    host.querySelectorAll('[data-action]').forEach(b=>b.addEventListener('click',()=>{
      const a=b.dataset.action;
      if(a.startsWith('y')){this.amplitude(a);return;}
      if(a==='in'||a==='out')this.zoom(a==='in'?.5:2,.5);
      else if(a==='reset')this.reset();else this.pan(a==='left'?-.25:.25);
    }));
    host.querySelector('form').addEventListener('submit',e=>{
      e.preventDefault();const inputs=host.querySelectorAll('[data-time]');
      const a=Number(inputs[0].value),b=Number(inputs[1].value);
      if([...inputs].some(x=>!x.value.trim())||!Number.isFinite(a)||!Number.isFinite(b)||a>=b||a<this.full[0]-1e-9||b>this.full[1]+1e-9){
        this.status.textContent='时间范围无效：开始须小于结束，且位于当前录波 / 证据窗口内。';this.status.classList.add('is-error');return;
      }
      this.setRange(a,b);this.fetchNow();
    });
    this.frame.addEventListener('wheel',e=>{
      if(this.dead||window.essUiBusy)return;e.preventDefault();
      const delta=e.deltaY*(e.deltaMode===1?16:e.deltaMode===2?200:1);
      this.zoom(Math.exp(Math.max(-1.3,Math.min(1.3,delta*.002))),this.fraction(e));
    },{passive:false});
    this.frame.addEventListener('dblclick',()=>this.reset());
    this.frame.addEventListener('keydown',e=>{
      const k=e.key;if(!['+','=','-','ArrowLeft','ArrowRight','Home'].includes(k))return;
      e.preventDefault();if(k==='Home')this.reset();else if(k.startsWith('Arrow'))this.pan(k==='ArrowLeft'?-.25:.25);else this.zoom(k==='-'?2:.5,.5);
    });
    this.frame.addEventListener('pointerdown',e=>{
      if(e.button!==0||this.dead)return;
      this.frame.setPointerCapture(e.pointerId);this.frame.focus({preventScroll:true});
      this.drag={id:e.pointerId,range:this.range.slice(),x:this.fraction(e),select:e.shiftKey};
      this.cursor.hidden=true;if(e.shiftKey){this.selection.hidden=false;this.selection.style.width='0px';}
      e.preventDefault();
    });
    this.frame.addEventListener('pointermove',e=>{
      const f=this.fraction(e);
      if(!this.drag){this.showCursor(e,f);return;}
      const d=this.drag;if(e.pointerId!==d.id)return;
      if(d.select){
        const rect=this.frame.getBoundingClientRect();
        this.selection.style.left=((62+Math.min(d.x,f)*921)/1000*rect.width)+'px';
        this.selection.style.width=(Math.abs(f-d.x)*921/1000*rect.width)+'px';
      }else{const shift=(d.x-f)*(d.range[1]-d.range[0]);this.setRange(d.range[0]+shift,d.range[1]+shift);}
    });
    this.frame.addEventListener('pointerup',e=>{
      if(!this.drag||this.drag.id!==e.pointerId)return;
      const d=this.drag;this.drag=null;this.selection.hidden=true;
      if(d.select){const f=this.fraction(e);if(Math.abs(f-d.x)>.004){const w=d.range[1]-d.range[0];this.setRange(d.range[0]+Math.min(f,d.x)*w,d.range[0]+Math.max(f,d.x)*w);}}
      if(this.frame.hasPointerCapture(e.pointerId))this.frame.releasePointerCapture(e.pointerId);
      if(this.range[0]!==d.range[0]||this.range[1]!==d.range[1])this.fetchNow();
    });
    this.frame.addEventListener('pointercancel',()=>{this.drag=null;this.selection.hidden=true;this.fetchNow();});
    this.frame.addEventListener('lostpointercapture',()=>{this.drag=null;this.selection.hidden=true;});
    this.frame.addEventListener('pointerleave',()=>{if(!this.drag)this.cursor.hidden=true;});
    this.nav.addEventListener('pointerdown',e=>{
      if(e.button!==0)return;e.preventDefault();this.nav.setPointerCapture(e.pointerId);
      this.navDrag={id:e.pointerId,x:e.clientX,range:this.range.slice()};
      if(!e.target.closest('.timeline-nav-window')){
        const rect=this.nav.getBoundingClientRect(),f=(e.clientX-rect.left)/rect.width;
        const center=this.full[0]+f*(this.full[1]-this.full[0]),w=this.range[1]-this.range[0];
        this.setRange(center-w/2,center+w/2);this.navDrag.range=this.range.slice();
      }
    });
    this.nav.addEventListener('pointermove',e=>{
      const d=this.navDrag;if(!d||d.id!==e.pointerId)return;
      const shift=(e.clientX-d.x)/this.nav.getBoundingClientRect().width*(this.full[1]-this.full[0]);
      this.setRange(d.range[0]+shift,d.range[1]+shift);
    });
    const endNav=()=>{this.navDrag=null;this.fetchNow();};
    this.nav.addEventListener('pointerup',endNav);this.nav.addEventListener('pointercancel',endNav);
    this.updateControls();this.fetchNow();
  }
  format(v){return Number(v.toPrecision(12)).toString();}
  fraction(e){const r=this.frame.getBoundingClientRect();return Math.max(0,Math.min(1,((e.clientX-r.left)/r.width*1000-62)/921));}
  updateControls(){
    const [a,b]=this.range;this.host.dataset.start=String(a);this.host.dataset.end=String(b);
    this.host.querySelector('[data-time=start]').value=this.format(a);this.host.querySelector('[data-time=end]').value=this.format(b);
    const span=b-a,full=this.full[1]-this.full[0];
    this.host.querySelector('.timeline-span').textContent=`跨度 ${this.format(span)} s · ${(full/span).toFixed(full/span>100?0:1)}×`;
    const nav=this.host.querySelector('.timeline-nav-window');nav.style.left=((a-this.full[0])/full*100)+'%';nav.style.width=(span/full*100)+'%';
  }
  setRange(a,b){
    if(this.dead||!Number.isFinite(a)||!Number.isFinite(b)||a>=b)return;
    const full=this.full[1]-this.full[0],min=Math.max(1e-9,full*1e-10);
    let width=Math.min(full,Math.max(min,b-a));a=(a+b-width)/2;b=a+width;
    if(a<this.full[0]){a=this.full[0];b=a+width;}if(b>this.full[1]){b=this.full[1];a=b-width;}
    this.range=[Math.max(a,this.full[0]),Math.min(b,this.full[1])];
    this.serial++;this.controller?.abort();clearTimeout(this.timer);this.cursor.hidden=true;
    this.updateControls();this.draw();this.status.classList.remove('is-error');this.status.textContent='正在读取当前时间窗口的原始采样…';
    this.timer=setTimeout(()=>this.fetchNow(),100);
  }
  zoom(factor,fraction){const [a,b]=this.range,anchor=a+(b-a)*fraction,w=(b-a)*factor;this.setRange(anchor-w*fraction,anchor+w*(1-fraction));}
  pan(fraction){const [a,b]=this.range,w=b-a;this.setRange(a+w*fraction,b+w*fraction);}
  reset(){this.yRange=null;this.setRange(...this.full);this.fetchNow();}
  amplitude(action){
    if(action==='yreset'){this.yRange=null;this.draw();return;}
    if(!this.data)return;
    if(!this.yRange){let lo=Infinity,hi=-Infinity;for(const p of [...this.data.trace,...(this.data.processed_trace||[])]){if(p[0]>=this.range[0]&&p[0]<=this.range[1]&&Number.isFinite(p[1])){lo=Math.min(lo,p[1]);hi=Math.max(hi,p[1]);}}
      if(!Number.isFinite(lo))return;const pad=(hi-lo)*.12||1;this.yRange=[lo-pad,hi+pad];}
    const mid=(this.yRange[0]+this.yRange[1])/2,w=(this.yRange[1]-this.yRange[0])*(action==='yin'?.5:2);
    if(Number.isFinite(w)&&w>1e-12){this.yRange=[mid-w/2,mid+w/2];this.draw();}
  }
  draw(){
    if(!this.data)return;
    this.svg.innerHTML=plot(this.data.trace,this.data.kind,this.data.unit,this.options.target??null,this.options.band||0,this.options.trigger??null,
      {domain:this.range,yDomain:this.yRange,gaps:this.data.gaps||[],digitalBins:this.data.digital_bins||[],
       overlayTrace:this.data.processed_trace||[],processedBins:this.data.processed_bins||[],
       repairMarkers:this.data.repair_markers||[],rejectedSpans:this.data.rejected_spans||[],
       showRaw:this.layers.raw,showProcessed:this.layers.processed,showRepairs:this.layers.repairs});
  }
  showCursor(e,f){
    if(!this.data)return;const x=this.range[0]+f*(this.range[1]-this.range[0]);
    this.cursor.hidden=false;this.cursor.textContent=this.format(x)+' s';
    this.cursor.style.left=Math.max(0,Math.min(this.frame.clientWidth-110,e.clientX-this.frame.getBoundingClientRect().left+10))+'px';
  }
  async fetchNow(){
    clearTimeout(this.timer);if(this.dead)return;this.controller?.abort();this.controller=new AbortController();
    const serial=++this.serial,query=new URLSearchParams({...this.options.parameters,start:String(this.range[0]),end:String(this.range[1]),limit:'1800'});
    this.host.dataset.loading='true';
    try{
      let data;
      if(this.options.loader){data=await this.options.loader(this.range[0],this.range[1],1800);}
      else{
        const response=await fetch('/api/trace?'+query.toString(),{headers:{'X-ESS-Token':window.ESS_TOKEN},signal:this.controller.signal});
        data=await response.json();if(!response.ok)throw Error(data.error||'波形读取失败');
      }
      if(this.dead||serial!==this.serial)return;
      this.data=data;this.host.dataset.mode=data.mode;this.host.dataset.samples=String(data.source_samples);
      this.host.dataset.loading='false';this.status.classList.remove('is-error');
      this.status.textContent=`${data.source_samples.toLocaleString()} 个原始样本 · ${data.display_points} 个显示点 · ${data.warning}`;
      if(!this.overview&&Math.abs(this.range[0]-this.full[0])<1e-9&&Math.abs(this.range[1]-this.full[1])<1e-9){
        this.overview=data;this.host.querySelector('.timeline-nav-bg').innerHTML=plot(data.trace,data.kind,'',null,0,null,{domain:this.full,gaps:data.gaps||[],digitalBins:data.digital_bins||[]}).replace('<svg ','<svg preserveAspectRatio="none" ');
      }
      this.draw();
    }catch(err){
      if(this.dead||serial!==this.serial||err.name==='AbortError')return;
      this.host.dataset.loading='false';this.status.classList.add('is-error');this.status.textContent=err.message;
      this.data=null;this.svg.innerHTML='<div class="empty">当前窗口读取失败，不显示旧波形。</div>';
    }
  }
  destroy(){this.dead=true;this.serial++;clearTimeout(this.timer);this.controller?.abort();WaveTimeline.instances.delete(this);}
  static clearWithin(parent){for(const item of [...WaveTimeline.instances])if(parent.contains(item.host))item.destroy();}
}
