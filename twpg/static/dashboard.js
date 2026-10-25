(()=>{'use strict';
 const base=window.TWC_BASE||'/twcrawler';const scope=window.TWC_LIVE_SCOPE||'';
 const statusFa={running:'در حال استخراج',pending:'در انتظار',partial:'ناقص',done:'تکمیل‌شده',failed:'خطادار',empty_review:'نیازمند بررسی'};
 const n=new Intl.NumberFormat('fa-IR');
 const by=(sel,root=document)=>root.querySelector(sel);
 const safeText=v=>String(v??'');
 const fmt=v=>n.format(Number(v)||0);
 const toggle=by('#navToggle');if(toggle)toggle.addEventListener('click',()=>document.body.classList.toggle('sidebar-open'));
 function setNum(el,next){if(!el)return;const old=el.dataset.raw===undefined?null:Number(el.dataset.raw);el.dataset.raw=next;el.textContent=fmt(next);if(old!==null&&old!==Number(next)){el.classList.remove('changed');void el.offsetWidth;el.classList.add('changed')}}
 const setStats=s=>{for(const [key,val]of Object.entries(s||{})){document.querySelectorAll(`[data-stat="${key}"]`).forEach(el=>setNum(el,val))}
  for(const [key,val]of Object.entries(s.job_states||{})){document.querySelectorAll(`[data-job="${key}"]`).forEach(el=>setNum(el,val))}
  const el=by('#crawlerState');if(el){el.classList.toggle('running',!!s.enabled);el.classList.toggle('paused',!s.enabled);by('#crawlerStateText').textContent=s.enabled?'استخراج مجاز':'استخراج متوقف'} };
 function setChannelCard(root,c){for(const k of ['programs','episodes','clips','done','remaining']){const el=by(`[data-c="${k}"]`,root);if(el)setNum(el,k==='remaining'?['pending','partial','failed','empty_review','running'].reduce((sum,s)=>sum+(+c[s]||0),0):c[k])}
 const bar=by('[data-progress]',root);if(bar)bar.style.width=(c.programs?100*c.done/c.programs:0).toFixed(1)+'%'}
 function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=safeText(text);return e}
 function fillJobs(rows){const body=by('#liveJobs');if(!body)return;body.replaceChildren();if(!rows.length){const tr=el('tr');const td=el('td','empty','موردی با این فیلترها یافت نشد.');td.colSpan=7;tr.append(td);body.append(tr);return}
  for(const j of rows){const tr=el('tr');tr.append(el('td','network-cell',j.networks||'بدون شبکه'));
   const title=el('td');const a=el('a','name-link',j.name);a.href=base+'/program/'+encodeURIComponent(j.program_key);title.append(a);tr.append(title);
   const state=el('td');state.append(el('span','pill st-'+(j.status in statusFa?j.status:'pending'),statusFa[j.status]||'نامشخص'));tr.append(state);
   tr.append(el('td','',fmt(j.episodes_found)));tr.append(el('td','',fmt(j.clips_found)));tr.append(el('td','',j.attempts));tr.append(el('td','muted',j.updated_at||'—'));body.append(tr)} }
 async function refresh(){if(!scope||document.hidden)return;
  const params=new URLSearchParams({scope});const filters=window.TWC_FILTERS||{};for(const [k,v]of Object.entries(filters))if(v)params.set(k,v);
  try{const r=await fetch(base+'/api/live?'+params.toString(),{credentials:'same-origin',cache:'no-store',headers:{'Accept':'application/json'}});
   if(r.status===401){location.href=base+'/login';return}if(!r.ok)throw Error('پاسخ نامعتبر');const data=await r.json();setStats(data.stats);
   const rows=data.channels||[];if(rows.length){for(const c of rows){document.querySelectorAll('[data-channel="'+CSS.escape(c.slug)+'"]').forEach(root=>setChannelCard(root,c));
     const detail=by('[data-channel-summary="'+CSS.escape(c.slug)+'"]');if(detail){setChannelCard(detail,c);for(const k of ['pending','partial','failed','running','empty_review'])setNum(by(`[data-counter="${k}"]`),c[k]);
     const pr=by('#channelProgress');if(pr)pr.style.width=(c.programs?100*c.done/c.programs:0).toFixed(1)+'%';const d=by('#channelDone');if(d)d.textContent=fmt(c.done)+' تکمیل‌شده'}}}
   if(data.jobs)fillJobs(data.jobs);const now=by('#lastUpdated');if(now)now.textContent=new Date().toLocaleTimeString('fa-IR',{hour:'2-digit',minute:'2-digit'});
   const live=by('#activityLive');if(live)live.textContent='آخرین همگام‌سازی: '+new Date().toLocaleTimeString('fa-IR');
  }catch(e){const now=by('#lastUpdated');if(now)now.textContent='قطع ارتباط موقت';}}
 window.control=async action=>{if(!['resume','pause'].includes(action))return;if(!confirm(action==='resume'?'اجازه ادامه استخراج داده شود؟':'اجازه استخراج متوقف شود؟'))return;
 try{const r=await fetch(base+'/api/crawler/'+action,{method:'POST',credentials:'same-origin',headers:{'X-Requested-With':'TWCrawlerUI'}});if(!r.ok)throw Error('عملیات انجام نشد');await refresh()}catch(e){alert('خطا: '+e.message)}};
 if(scope){refresh();setInterval(refresh,12000)}
})();
