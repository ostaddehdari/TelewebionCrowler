() => {
  const step=Math.max(300,Math.floor(innerHeight*0.56));const old=window.scrollY;
  window.scrollBy(0,step);
  if(window.scrollY>old+2)return {moved:true,where:'window'};
  for(const e of document.querySelectorAll('[data-virtuoso-scroller], [style*="overflow"],main,section,div')){
    if(e.clientHeight<180||e.scrollHeight-e.clientHeight<50)continue;
    const s=getComputedStyle(e);
    if(!['auto','scroll'].includes(s.overflowY))continue;
    const oldTop=e.scrollTop;e.scrollTop+=step;
    if(e.scrollTop>oldTop+2)return {moved:true,where:'custom'};
  }
  return {moved:false,where:'none'};
}
