"""Resumable per-parent metadata crawler: V4 parent catalog remains unchanged.

No media stream / DRM / authentication bypass. Official public pages & observed JSON only.
"""
from __future__ import annotations
import asyncio,os, json, re, hashlib, datetime
from urllib.parse import urlsplit, urljoin
from playwright.async_api import async_playwright
from .utils import utc

BASE='https://telewebion.net'
GATE='https://gateway.telewebion.net/kandoo/vod/content/get-kids-serial-season'
ALLOWED={'telewebion.net','www.telewebion.net','gateway.telewebion.net','static.telewebion.net'}
MIME_TYPES=('application/json','text/json')

def own_link(s):
 if not isinstance(s,str) or not s:return None
 try:
  u=urlsplit(urljoin(BASE,s));return u.geturl() if u.scheme=='https' and u.hostname in ALLOWED else None
 except ValueError:return None

def picture(obj):
 if isinstance(obj,str):return own_link(obj) if obj.startswith(('https://','/')) else None
 if isinstance(obj,dict):
  for k in ('image_url','imageUrl','image','thumbnail','thumb','poster','cover','url','src'):
   v=obj.get(k)
   if isinstance(v,str) and (r:=picture(v)) and any(x in r for x in ('static.telewebion','image','poster','thumbnail')):return r
 return None

def first(obj,*keys):
 for k in keys:
  x=obj.get(k)
  if x is not None and x!='':return x
 return None

def intmaybe(v):
 try:return int(v) if v is not None and str(v).strip().isdigit() else None
 except (TypeError,ValueError):return None

def dateish(v):
 if v is None:return None
 s=str(v).strip()
 if not s:return None
 if len(s)>80:return None
 if isinstance(v,(int,float)) and v>1_000_000_000:
  try:
   ts=float(v)/1000 if float(v)>10_000_000_000 else float(v)
   return datetime.datetime.fromtimestamp(ts,datetime.timezone.utc).isoformat()
  except (OverflowError,OSError,ValueError):pass
 return s

def normalize(item,program,forced=None,source='api'):
 """Strict: only known child item forms; never treat a full series record as its own episode."""
 if not isinstance(item,dict):return None
 ctype=str(item.get('content_type') or item.get('type') or '').upper()
 raw_url=first(item,'episode_url','page_url','web_url','share_url','link','url')
 url=own_link(raw_url) if isinstance(raw_url,str) else None
 is_ep=bool(url and '/episode/' in urlsplit(url).path)
 is_serialpart=ctype in ('SERIALPARTS','EPISODE','EPISODES','PROGRAM_EPISODE')
 is_clip=ctype in ('CLIP','PROGRAMCLIP','PROGRAM_CLIP') or forced=='clip'
 number=intmaybe(first(item,'episode_number','episode','part_number','part'))
 content_id=str(first(item,'content_id','episode_id','clip_id','id','nid') or '')
 if not (is_ep or is_serialpart or is_clip or (forced=='episode' and number is not None and ctype not in ('SERIAL','MOVIE','PRODUCT','PROGRAM'))):return None
 kind='clip' if is_clip else ('episode' if forced != 'clip' else 'clip')
 if url and '/episode/' not in urlsplit(url).path:url=None
 # An alias may be shared by every child (example /90a9e); do not create a bogus episode URL.
 if not re.fullmatch(r'0x[0-9a-fA-F]+',content_id):
  if url and (m:=re.search(r'/episode/(0x[0-9a-fA-F]+)',url)):content_id=m.group(1)
  else:content_id=None
 title=str(first(item,'special_title','clip_title','episode_title','title','persian_title','name') or '').strip()
 if not title:
  if number is None:return None
  title=f'قسمت {number}'
 if len(title)>350:return None
 season=intmaybe(first(item,'season','season_number'))
 duration=first(item,'duration_seconds','durationSeconds','duration')
 # Kids sample uses duration in MINUTES; only explicit seconds conversion for serial parts.
 duration=intmaybe(duration)
 if duration is not None and ctype=='SERIALPARTS' and 'duration_seconds' not in item:duration*=60
 return dict(program_key=program['program_key'],kind=kind,provider_id=content_id,title=title,
  subtitle=first(item,'subtitle','sub_title'),source_url=url,
  image_url=picture(item.get('media')) or picture(item) or program.get('image_url'),
  description=first(item,'story','description','summary','text'),season=season,episode_number=number,
  duration_seconds=duration,broadcast_date=dateish(first(item,'broadcast_date','broadcastDate','broadcast_at','air_date','airDate','gregorian_date','persian_date')),
  published_at=dateish(first(item,'published_at','publishedAt','created_at','date','time')),view_count=intmaybe(first(item,'view_count','views')),
  source_type=source,raw=item)

def parts_from_json(payload,program,forced=None,source='api',limit=2000):
 found=[];known=set()
 def visit(obj,depth=0):
  if len(found)>=limit or depth>11:return
  if isinstance(obj,dict):
   item=normalize(obj,program,forced,source)
   if item:
    unique=(item.get('provider_id'),item.get('source_url'),item.get('season'),item.get('episode_number'),item['title'],item['kind'])
    if unique not in known:known.add(unique);found.append(item)
   for k,v in obj.items():
    if isinstance(v,(dict,list)) and k not in ('casts','person','genres','media','countries','languages','format'):
     visit(v,depth+1)
  elif isinstance(obj,list):
   for el in obj[:5000]:visit(el,depth+1)
 visit(payload)
 return found

def season_numbers(payload):
 seasons=set()
 if not isinstance(payload,dict):return []
 def rec(o,depth=0):
  if depth>7:return
  if isinstance(o,dict):
   for key,value in o.items():
    if key=='seasons' and isinstance(value,list):
     for x in value:
      if isinstance(x,dict):x=first(x,'number','season','season_number')
      n=intmaybe(x)
      if n and n<=500:seasons.add(n)
    if key in ('season','season_number'):
     n=intmaybe(value)
     if n and n<=500:seasons.add(n)
    if isinstance(value,(dict,list)) and key not in ('casts','media','person'):rec(value,depth+1)
  elif isinstance(o,list):
   for x in o[:100]:rec(x,depth+1)
 rec(payload)
 return sorted(seasons)

DOM_JS=r'''() => {
 const out=[];const rootAttr = e => e.closest('[data-row-type]');
 const seen=new Set();
 const txt=e=>(e?.textContent||'').replace(/\s+/g,' ').trim();
 const image=e=> {const im=e?.querySelector('img[data-src],img[src]');return im?.getAttribute('data-src')||im?.currentSrc||im?.getAttribute('src')||''};
 for(const a of document.querySelectorAll('a[href]')){
   const href=a.href; if(!/^https:\/\/telewebion\.net\/episode\/0x[0-9a-f]+/i.test(href))continue;
   let box=a.closest('[data-gtm="card"], [data-index],article,li,[class*="card"], [data-row-type]')||a.parentElement;
   let row=rootAttr(a);
   const rowType=row?.getAttribute('data-row-type')||'';
   let title=a.getAttribute('title')||a.getAttribute('data-tile')||txt(a.querySelector('h3,h2,h4'))||txt(box?.querySelector('h3,h2,h4'))||a.querySelector('img')?.alt||txt(a);
   title=title.slice(0,260);
   if (!title || title==='مشاهده' || title==='تماشا')continue;
   const key=href+'|'+rowType;
   if(seen.has(key))continue;seen.add(key);
   const text=txt(box).slice(0,420);
   const imgs=image(box)||image(a);
   const time=box?.querySelector('time')?.getAttribute('datetime')||'';
   out.push({url:href,title,image_url:imgs,text,row_type:rowType,date:time,
     season:(text.match(/فصل\s*([0-9۰-۹]+)/)||[])[1]||'',
     episode:(text.match(/قسمت\s*([0-9۰-۹]+)/)||[])[1]||''});
 }
 // Also capture a programClip card whose page does not expose an /episode/ anchor.
 // Require a card-level unique ID; never promote an entire program row to a clip.
 for(const row of document.querySelectorAll('[data-row-type="programClip"]')){
   for(const card of row.querySelectorAll('[data-gtm="card"][data-id], a[data-id]')){
     const id=card.getAttribute('data-id')||'';
     if(!/^0x[0-9a-f]+$/i.test(id))continue;
     const a=card.matches('a')?card:card.querySelector('a[href]');
     const u=a?.href||'';
     if(u && /\/(?:program|product|kids)\//.test(u))continue;
     const t=card.getAttribute('data-tile')||card.getAttribute('title')||txt(card.querySelector('h3,h2,h4'))||card.querySelector('img')?.alt||'';
     if(!t)continue;
     const key='clip:'+id;
     if(seen.has(key))continue;seen.add(key);
     out.push({provider_id:id,url:/^https:\/\/telewebion\.net\/episode\//i.test(u)?u:'',title:t,image_url:image(card),text:txt(card).slice(0,420),row_type:'programClip',date:'',season:'',episode:''});
   }
 }
 const more=[];
 for(const b of document.querySelectorAll('button')){
   const s=txt(b),g=b.getAttribute('data-gtm')||'';
   if(!b.disabled && (/(بیشتر|نمایش بیشتر|موارد بیشتر|load more)/i.test(s)||/load.more/i.test(g)))more.push({text:s,gtm:g});
 }
 return {items:out,more,atBottom:window.scrollY + window.innerHeight >= document.documentElement.scrollHeight-8};
}'''

def tofaint(s):
 if s is None:return None
 return intmaybe(str(s).translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹','0123456789')))

def dom_normalize(raw,program,source):
 m=re.search(r'/episode/(0x[0-9a-f]+)',raw.get('url',''),re.I)
 unique=raw.get('provider_id') if re.fullmatch(r'0x[0-9a-f]+',str(raw.get('provider_id') or ''),re.I) else (m.group(1) if m else None)
 if not unique:return None
 kind='clip' if raw.get('row_type')=='programClip' else 'episode'
 text=raw.get('text') or ''
 date=raw.get('date') or None
 if not date:
  found=re.search(r'(?:[۱۲۳۴۵۶۷۸۹۰0-9]{4}[/\-][۱۲۳۴۵۶۷۸۹۰0-9]{1,2}[/\-][۱۲۳۴۵۶۷۸۹۰0-9]{1,2})',text)
  if found:date=found.group(0)
 return dict(program_key=program['program_key'],kind=kind,provider_id=unique,title=raw['title'],subtitle=None,source_url=raw.get('url') or None,image_url=own_link(raw.get('image_url')) or program.get('image_url'),description=text or None,season=tofaint(raw.get('season')),episode_number=tofaint(raw.get('episode')),duration_seconds=None,broadcast_date=date,published_at=None,view_count=None,source_type=source,raw=raw)

async def launch(p,headed=False):
 last=None
 for kwargs,label in [({'channel':'chrome'},'installed Chrome'),({'channel':'msedge'},'installed Edge'),
                      ({'executable_path':'/usr/bin/chromium'},'system Chromium') if os.path.isfile('/usr/bin/chromium') else ({},'Playwright Chromium'),
                      ({},'Playwright Chromium')]:
  try:
   b=await p.chromium.launch(headless=not headed,**kwargs)
   print('[BROWSER]',label,flush=True);return b
  except Exception as exc:last=exc
 raise RuntimeError(f'Could not start Chrome / Edge / Chromium: {last}')

async def scrape(browser,program,opts,progress):
 """Every child captured is metadata; does not fetch .mp4/.m3u8."""
 context=await browser.new_context(locale='fa-IR',viewport={'width':1366,'height':900},service_workers='block')
 page=await context.new_page();page.set_default_timeout(18000)
 allitems={};dirty={};seasons={1} if program['kind']=='kids' else set();notes=[];urls=[];parent_meta=None
 detected_responses=[];tasks=[]
 def collect(items):
  for item in items:
   if not item or not item.get('title'):continue
   ident=item.get('provider_id') or item.get('source_url') or f'{item.get("season")}:{item.get("episode_number")}:{item["title"]}'
   key=f'{item["kind"]}|{ident}'
   old=allitems.get(key)
   if item['kind']=='clip' and (ident or ''):
    allitems.pop('episode|'+str(ident),None)
   if item['kind']=='episode' and 'clip|'+str(ident) in allitems:continue
   if old:
    for f in ('broadcast_date','published_at','season','episode_number','source_url','description','image_url','duration_seconds'):
     if not old.get(f) and item.get(f):old[f]=item[f]
    if old.get('source_type','').startswith('dom') and item.get('source_type','').startswith('json'):
     old['raw']=item['raw'];old['source_type']=item['source_type']
   else:allitems[key]=item
   dirty[key]=allitems[key]
 async def sniff(resp):
  try:
   u=urlsplit(resp.url)
   if u.hostname not in ALLOWED or 'json' not in (resp.headers.get('content-type') or ''):return
   clen=int(resp.headers.get('content-length','0') or '0')
   if clen>7_000_000:return
   if not any(s in u.path.lower() for s in ('episode','clip','archive','season','serial','content','program')):return
   data=await resp.json();ex=parts_from_json(data,program,source='json_observed')
   if ex:
    detected_responses.append({'url':resp.url,'children':len(ex)});collect(ex)
   seasons.update(season_numbers(data))
  except Exception:pass
 page.on('response',lambda r:tasks.append(asyncio.create_task(sniff(r))))
 async def visit(url,label):
  try:
   response=await page.goto(url,wait_until='domcontentloaded',timeout=60000)
   if response and response.status>=400:raise RuntimeError(f'HTTP {response.status}: {url}')
   urls.append(url);await page.wait_for_timeout(850)
   plateau=0;oldcount=-1;more_clicks=0;atbottom=0
   for step in range(opts.max_scrolls):
    d=await page.evaluate(DOM_JS)
    collect([dom_normalize(x,program,'dom_'+label) for x in d['items']])
    if len(allitems)==oldcount:plateau+=1
    else:plateau=0
    oldcount=len(allitems)
    if step%15==0:
     batch=list(dirty.values());dirty.clear()
     await progress(len(allitems),label,step,batch)
    if more_clicks<opts.max_more and d['more'] and (d['atBottom'] or step%3==0):
     try:
      target=page.locator('button[data-gtm="kids-clip-sidebar-load-more"]:visible').first
      if not await target.count():target=page.get_by_role('button',name=re.compile('بیشتر|load more',re.I)).last
      if await target.count():await target.click(timeout=3000);more_clicks+=1
     except Exception:pass
    try:
     await page.evaluate('''() => {const ss=[...document.querySelectorAll('[data-virtuoso-scroller="true"]')];if(ss.length){ss.forEach(x=>{x.scrollTop+=Math.max(500,x.clientHeight*0.85)});}window.scrollBy(0, Math.max(600,innerHeight*0.85));}''')
    except Exception:pass
    await page.wait_for_timeout(opts.delay_ms)
    if d['atBottom']:atbottom+=1
    else:atbottom=0
    if plateau>=12 and (atbottom>=8 or step>=32):break
   return True
  except Exception as exc:
   notes.append(f'{label}: {type(exc).__name__}: {str(exc)[:230]}');return False
 try:
  base=program['url'].rstrip('/')
  if program['kind']=='program':
   await visit(base+'/archive','archive')
   await visit(base,'program_main')
  else:
   await visit(base,'detail')
  if tasks:await asyncio.gather(*tasks,return_exceptions=True)
  # Visible season tabs: discover season IDs even when JSON uses only current season.
  try:
   tab_nums=await page.evaluate(r'''() => {
     const translate=s=>s.replace(/[۰-۹]/g,x=>String(x.charCodeAt(0)-1776));
     const nums=[];
     for(const el of document.querySelectorAll('button,[role="tab"],select option')){
       const t=(el.textContent||'').replace(/\s+/g,' ').trim();
       const m=translate(t).match(/^(?:فصل|Season)\s*([0-9]{1,3})$/i);
       if(m)nums.push(Number(m[1]));
     }
     return [...new Set(nums)].filter(x=>x>=1 && x<=500);
   }''')
   seasons.update(tab_nums)
  except Exception:pass
  if program['kind']=='kids':
   # Use the endpoint reported in the user's actual browser network traffic.
   # Each page returns body.content[] with serial_parts[] and possible seasons[].
   ident=program['provider_id'];loaded=set();seenpages=set();pending=sorted(seasons or [1])
   si=0
   while si<len(pending) and si<opts.max_seasons:
    season=pending[si];si+=1
    offset=0
    for pi in range(opts.max_api_pages):
     params={'content_id':ident,'season':season,'first':opts.page_size,'offset':offset}
     url=GATE+'?'+ '&'.join(f'{k}={v}' for k,v in params.items())
     try:
      response=await context.request.get(url,headers={'Referer':base,'Origin':BASE,'Accept':'application/json'},timeout=25000)
      if response.status in (401,403,404,429):
       notes.append(f'kids_api HTTP {response.status} season={season} offset={offset}');break
      if not response.ok:raise RuntimeError('HTTP '+str(response.status))
      payload=await response.json()
      if parent_meta is None and isinstance(payload,dict):
       pc=payload.get('body',{}).get('content',[]) if isinstance(payload.get('body'),dict) else []
       if isinstance(pc,list):
        for x in pc:
         if isinstance(x,dict) and x.get('content_id')==ident:
          parent_meta={k:v for k,v in x.items() if k!='serial_parts'};break
      batch=[i for i in parts_from_json(payload,program,forced='episode',source='kids_season_api') if i['kind']=='episode']
      ids={i.get('provider_id') or f'{i.get("season")}-{i.get("episode_number")}-{i["title"]}' for i in batch}
      sign=tuple(sorted(ids))
      if not batch or not (ids-loaded) or sign in seenpages:break
      seenpages.add(sign);loaded.update(ids);collect(batch)
      # New seasons found in the response, but don't trust guessed season counts.
      extra=season_numbers(payload)
      for n in extra:
       if n not in pending and len(pending)<opts.max_seasons:pending.append(n)
      batch_dirty=list(dirty.values());dirty.clear()
      await progress(len(allitems),'kids_season_'+str(season),pi,batch_dirty)
      offset+=opts.page_size
      if len(batch)<opts.page_size:break
      await asyncio.sleep(max(opts.delay_ms/1000,0.4))
     except Exception as exc:
      notes.append(f'kids_api season={season} offset={offset}: {type(exc).__name__}: {str(exc)[:120]}');break
   # Inspect detected seasons from current page including user-visible tabs if present.
  if program['kind']=='product' and len(seasons)>1:
   # Visit other visible seasons; responses captured by the same API observer.
   # Do not assume every season loads correctly when a UI click fails.
   for sn in sorted(seasons)[:opts.max_seasons]:
    try:
     label=re.compile(r'^(?:فصل|Season)\s*'+str(sn)+r'$',re.I)
     tabs=page.get_by_role('button',name=label)
     if not await tabs.count():tabs=page.get_by_role('tab',name=label)
     if not await tabs.count():continue
     await tabs.first.click(timeout=4000)
     await page.wait_for_timeout(800)
     for ss in range(min(25,opts.max_scrolls)):
      snapshot=await page.evaluate(DOM_JS)
      collect([dom_normalize(x,program,'dom_season_'+str(sn)) for x in snapshot['items']])
      await page.evaluate('window.scrollBy(0,Math.max(700,innerHeight*.9))')
      await page.wait_for_timeout(opts.delay_ms)
     batch_dirty=list(dirty.values());dirty.clear()
     await progress(len(allitems),'product_season_'+str(sn),0,batch_dirty)
    except Exception as exc:notes.append(f'product season {sn}: {str(exc)[:120]}')
  if tasks:await asyncio.gather(*tasks,return_exceptions=True)
 finally:
  await context.close()
 return {'items':list(allitems.values()),'notes':notes,'observed_api':detected_responses[:25],
   'urls':urls,'season_numbers':sorted(seasons),'checked_at':utc(),
   'parent_metadata':parent_meta,'coverage':'partial_or_end_observed_not_guaranteed_complete'}
