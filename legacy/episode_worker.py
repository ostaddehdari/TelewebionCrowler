"""CLI worker: use existing SQLite queue; Chrome/Edge installed; retry and resume."""
from __future__ import annotations
import argparse,asyncio,json,traceback,socket,sys,os,signal
from episode_db import conn,init,log,update_item,utc,DB
from episode_crawler import async_playwright,launch,scrape

async def run_one(browser,r,args,ident):
 key=r['program_key'];name=r['name'];now=utc()
 with conn() as c:
  c.execute("UPDATE jobs SET status='running',attempts=attempts+1,started_at=?,updated_at=?,worker=?,last_error=NULL WHERE program_key=?",(now,now,ident,key))
  log(c,'start',f'شروع استخراج: {name}',key)
 async def progress(count,source,step,batch=None):
  if step%15!=0 and not batch:return
  with conn() as c:
   for it in batch or []:
    update_item(c,it)
   counts={v['kind']:v['n'] for v in c.execute('SELECT kind,COUNT(*) n FROM media_items WHERE program_key=? GROUP BY kind',(key,))}
   c.execute('UPDATE jobs SET updated_at=?,episodes_found=?,clips_found=? WHERE program_key=?',(utc(),counts.get('episode',0),counts.get('clip',0),key))
   log(c,'progress',f'{name} | مرحله {source} | گام {step} | {count} قطعه شناسایی‌شده',key)
 try:
  data=await scrape(browser,dict(r),args,progress)
  items=data['items'];episodes=clips=0
  with conn() as c:
   if data.get('parent_metadata'):
    c.execute('''INSERT INTO program_metadata(program_key,source,details_json,updated_at) VALUES(?,?,?,?) ON CONFLICT(program_key) DO UPDATE SET source=excluded.source,details_json=excluded.details_json,updated_at=excluded.updated_at''',
      (key,'kids_season_api',json.dumps(data['parent_metadata'],ensure_ascii=False),utc()))
   for i,item in enumerate(items):
    if item['kind']=='clip' and item.get('provider_id'):
     c.execute("DELETE FROM media_items WHERE program_key=? AND kind='episode' AND provider_id=?",(key,item['provider_id']))
    if update_item(c,item):
     if item['kind']=='clip':clips+=1
     else:episodes+=1
    if i%100==0:c.commit()
   c.execute('''UPDATE jobs SET status=?,episodes_found=?,clips_found=?,finished_at=?,updated_at=?,last_error=? WHERE program_key=?''',
    ('partial' if data['notes'] else ('empty_review' if not items else 'done'),episodes,clips,utc(),utc(),'; '.join(data['notes'])[:1000] or None,key))
   log(c,'warning' if data['notes'] else 'done',f'{name} | قسمت {episodes} | کلیپ {clips}',key,
    {'notes':data['notes'],'urls':data['urls'],'observed_api':data['observed_api'],'seasons':data['season_numbers']})
  print(f'[SAVE] {key} {name} episodes={episodes} clips={clips} warnings={len(data["notes"])}',flush=True)
 except Exception as exc:
  msg=f'{type(exc).__name__}: {exc}'
  with conn() as c:
   c.execute("UPDATE jobs SET status='failed',finished_at=?,updated_at=?,last_error=? WHERE program_key=?",(utc(),utc(),msg[:1000],key))
   log(c,'error',f'{name} | {msg[:500]}',key)
  print(f'[FAILED] {key}: {msg}',flush=True)

async def run(args):
 init()
 with conn() as c:
  # Any stale 'running' jobs were interrupted in previous runs; safe to retry.
  c.execute("UPDATE jobs SET status='pending',worker=NULL WHERE status='running'")
  log(c,'info','راه‌اندازی پردازش‌گر قسمت‌ها',details={'workers':args.workers,'limit':args.limit})
 qclauses=["j.status IN ('pending','failed')","j.attempts<?"]
 par=[args.max_attempts]
 if args.retry_partial:qclauses[0]="j.status IN ('pending','failed','partial','empty_review')"
 if args.kind:qclauses.append('p.kind=?');par.append(args.kind)
 if args.slug:qclauses.append('EXISTS(SELECT 1 FROM program_channels pc WHERE pc.program_key=p.program_key AND pc.slug=?)');par.append(args.slug)
 if args.program:qclauses.append('p.program_key=?');par.append(args.program)
 where=' AND '.join(qclauses)
 with conn() as c:
  jobs=[dict(r) for r in c.execute(f'''SELECT p.*,j.attempts,j.status FROM programs p JOIN jobs j ON p.program_key=j.program_key WHERE {where} ORDER BY
 CASE WHEN p.kind='kids' THEN 0 WHEN p.kind='program' THEN 1 ELSE 2 END,p.program_key''',par)]
 if args.limit:jobs=jobs[:args.limit]
 print(f'[START] DB={DB} jobs={len(jobs)} workers={args.workers}',flush=True)
 if not jobs:return
 async with async_playwright() as p:
  browser=await launch(p,args.headed)
  try:
   q=asyncio.Queue()
   for item in jobs:q.put_nowait(item)
   async def runner(i):
    ident=f'{socket.gethostname()}:{os.getpid()}:{i}'
    while not q.empty():
     with conn() as c:
      enabled=c.execute("SELECT value FROM settings WHERE key='enabled'").fetchone()
     if enabled and enabled['value']!='1':
      await asyncio.sleep(2);continue
     try:r=q.get_nowait()
     except asyncio.QueueEmpty:break
     try:await run_one(browser,r,args,ident)
     finally:q.task_done()
     await asyncio.sleep(max(args.delay_ms/1000,0.4))
   await asyncio.gather(*(runner(i) for i in range(args.workers)))
  finally:await browser.close()
 with conn() as c:log(c,'info','پردازش‌گر صف فعلی را تمام کرد')
 print('[FINISHED] queue batch complete. Re-run to pick newly imported jobs.',flush=True)

def parse():
 a=argparse.ArgumentParser(description='Telewebion episodes/clips metadata -> SQLite')
 a.add_argument('--workers',type=int,default=2)
 a.add_argument('--limit',type=int,default=0,help='0 = all; use 5 for smoke test')
 a.add_argument('--slug',default='',help='e.g. irinn, tv1, tw-kids')
 a.add_argument('--kind',choices=['kids','program','product'])
 a.add_argument('--program',default='',help='e.g. program:0x16b69aa2')
 a.add_argument('--headed',action='store_true')
 a.add_argument('--max-scrolls',type=int,default=160)
 a.add_argument('--max-more',type=int,default=120)
 a.add_argument('--max-seasons',type=int,default=40)
 a.add_argument('--max-api-pages',type=int,default=300)
 a.add_argument('--page-size',type=int,default=10)
 a.add_argument('--delay-ms',type=int,default=850)
 a.add_argument('--max-attempts',type=int,default=3)
 a.add_argument('--retry-partial',action='store_true')
 return a.parse_args()

if __name__=='__main__':
 args=parse()
 if not 1<=args.workers<=3:sys.exit('workers: 1..3')
 if not (args.page_size>=1 and 0<=args.limit and args.delay_ms>=450):sys.exit('Bad crawler bounds')
 try:asyncio.run(run(args))
 except KeyboardInterrupt:print('Interrupted. Checkpoints remain in SQLite.',flush=True)
