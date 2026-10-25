"""PostgreSQL-native crawl queue: reuses the user's 2026 Playwright scraping logic.

Single worker by default, row-level claim with SKIP LOCKED, incremental upserts,
conservative rate limits and restart-safe job state.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import os
import socket
import traceback
from . import scraper
from .db import connect, event, initialize
from .utils import utc

UPSERT_ITEM = '''
INSERT INTO media_items(item_key,program_key,kind,provider_id,title,subtitle,source_url,image_url,
 description,season,episode_number,duration_seconds,broadcast_date,published_at,view_count,
 source_type,raw_json,first_seen,last_seen)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT(item_key) DO UPDATE SET
 title=EXCLUDED.title,subtitle=COALESCE(EXCLUDED.subtitle,media_items.subtitle),
 source_url=COALESCE(EXCLUDED.source_url,media_items.source_url),
 image_url=COALESCE(EXCLUDED.image_url,media_items.image_url),
 description=COALESCE(EXCLUDED.description,media_items.description),
 season=COALESCE(EXCLUDED.season,media_items.season),
 episode_number=COALESCE(EXCLUDED.episode_number,media_items.episode_number),
 duration_seconds=COALESCE(EXCLUDED.duration_seconds,media_items.duration_seconds),
 broadcast_date=COALESCE(EXCLUDED.broadcast_date,media_items.broadcast_date),
 published_at=COALESCE(EXCLUDED.published_at,media_items.published_at),
 view_count=COALESCE(EXCLUDED.view_count,media_items.view_count),
 source_type=EXCLUDED.source_type,raw_json=EXCLUDED.raw_json,last_seen=EXCLUDED.last_seen
'''

def item_identity(item: dict) -> str | None:
    key=item['program_key'];kind=item['kind'];pid=item.get('provider_id')
    if pid: unique=f'{key}|{kind}|id:{pid.lower()}'
    elif item.get('source_url'): unique=f"{key}|{kind}|url:{item['source_url']}"
    else:
        if item.get('season') is None and item.get('episode_number') is None: return None
        unique=f'{key}|{kind}|s:{item.get("season")}|e:{item.get("episode_number")}|{item["title"]}'
    return hashlib.sha256(unique.encode('utf-8')).hexdigest()[:32]

def save_items(c, items: list[dict]) -> None:
    for item in items:
        item_key = item_identity(item)
        if not item_key: continue
        key = item['program_key']
        if item['kind']=='clip' and item.get('provider_id'):
            c.execute("DELETE FROM media_items WHERE program_key=%s AND kind='episode' AND provider_id=%s",(key,item['provider_id']))
        now=utc()
        c.execute(UPSERT_ITEM,(
            item_key,key,item['kind'],item.get('provider_id'),item['title'],item.get('subtitle'),
            item.get('source_url'),item.get('image_url'),item.get('description'),item.get('season'),
            item.get('episode_number'),item.get('duration_seconds'),item.get('broadcast_date'),
            item.get('published_at'),item.get('view_count'),item.get('source_type'),
            json.dumps(item.get('raw',{}),ensure_ascii=False,separators=(',',':')),now,now))

def claim(args, ident: str):
    """Atomically claim one eligible job; allows multiple worker processes safely."""
    clauses = ["j.attempts < %s", "j.status = ANY(%s)"]
    values = [args.max_attempts, ['pending','failed','partial','empty_review'] if args.retry_partial else ['pending','failed']]
    if args.kind: clauses.append('p.kind=%s');values.append(args.kind)
    if args.slug:
        clauses.append('EXISTS (SELECT 1 FROM program_channels pc WHERE pc.program_key=p.program_key AND pc.slug=%s)')
        values.append(args.slug)
    if args.program: clauses.append('p.program_key=%s');values.append(args.program)
    where = ' AND '.join(clauses)
    with connect() as c:
        with c.transaction():
            row = c.execute(f'''SELECT p.* FROM programs p JOIN jobs j USING(program_key)
                WHERE {where} ORDER BY CASE j.status WHEN 'pending' THEN 0 WHEN 'failed' THEN 1 WHEN 'partial' THEN 2 ELSE 3 END, CASE p.kind WHEN 'kids' THEN 0 WHEN 'program' THEN 1 ELSE 2 END,p.program_key
                FOR UPDATE OF j SKIP LOCKED LIMIT 1''',values).fetchone()
            if row:
                c.execute("""UPDATE jobs SET status='running',attempts=attempts+1,started_at=%s,
                     updated_at=%s,worker=%s,last_error=NULL WHERE program_key=%s""",
                    (utc(),utc(),ident,row['program_key']))
                event(c,'start','شروع استخراج: '+row['name'],row['program_key'])
            return row

def counters(c,key):
    values = {r['kind']:r['n'] for r in c.execute('SELECT kind,count(*) AS n FROM media_items WHERE program_key=%s GROUP BY kind',(key,))}
    return values.get('episode',0),values.get('clip',0)

async def process(browser,program,args):
    key=program['program_key'];name=program['name']
    async def progress(count,source,step,batch=None):
        if step%15!=0 and not batch:return
        with connect() as c:
            save_items(c,batch or [])
            ep,cl=counters(c,key)
            c.execute('UPDATE jobs SET episodes_found=%s,clips_found=%s,updated_at=%s WHERE program_key=%s',
                      (ep,cl,utc(),key))
    try:
        result=await scraper.scrape(browser,dict(program),args,progress)
        with connect() as c:
            save_items(c,result['items'])
            if result.get('parent_metadata'):
                c.execute('''INSERT INTO program_metadata(program_key,source,details_json,updated_at)
                  VALUES(%s,%s,%s,%s) ON CONFLICT(program_key) DO UPDATE SET
                  source=EXCLUDED.source,details_json=EXCLUDED.details_json,updated_at=EXCLUDED.updated_at''',
                  (key,'kids_season_api',json.dumps(result['parent_metadata'],ensure_ascii=False),utc()))
            ep,cl=counters(c,key)
            notes=result.get('notes',[])
            status='partial' if notes else ('empty_review' if ep+cl==0 else 'done')
            c.execute('''UPDATE jobs SET status=%s,episodes_found=%s,clips_found=%s,
               finished_at=%s,updated_at=%s,last_error=%s WHERE program_key=%s''',
               (status,ep,cl,utc(),utc(),'; '.join(notes)[:1000] or None,key))
            event(c,'warning' if notes else 'done',f'{name}: episodes={ep}, clips={cl}',key,
                  json.dumps({'notes':notes[:8],'urls':result['urls'][:10]},ensure_ascii=False))
        print(f'[SAVE] {key} {status} episodes={ep} clips={cl}',flush=True)
    except Exception as exc:
        message=f'{type(exc).__name__}: {exc}'
        with connect() as c:
            c.execute('''UPDATE jobs SET status='failed',finished_at=%s,updated_at=%s,
              last_error=%s WHERE program_key=%s''',(utc(),utc(),message[:1000],key))
            event(c,'error',f'{name}: {message[:450]}',key)
        print(f'[FAIL] {key}: {message}',flush=True)

async def run(args):
    initialize()
    # Running state from a crashed previous worker becomes eligible after 30 mins.
    with connect() as c:
        c.execute("""UPDATE jobs SET status='pending',worker=NULL WHERE status='running'
          AND (updated_at IS NULL OR (CASE WHEN updated_at ~ '^\\d{4}-\\d{2}-\\d{2}T' THEN LEFT(updated_at,19)::timestamp ELSE NULL END)<NOW()-INTERVAL '30 minutes')""")
    if not args.force:
        with connect() as c:
            row=c.execute("SELECT value FROM settings WHERE key='enabled'").fetchone()
        if not row or row['value']!='1':
            print('[PAUSED] Crawler disabled; start with --force for manual pilot or enable via dashboard')
            return
    if args.workers!=1:
        raise ValueError('Conservative mode supports exactly 1 browser worker per process')
    async with scraper.async_playwright() as playwright:
        browser=await scraper.launch(playwright,headed=args.headed)
        try:
            for _ in range(args.limit):
                if not args.force:
                    with connect() as c:
                        enabled=c.execute("SELECT value FROM settings WHERE key='enabled'").fetchone()
                    if not enabled or enabled['value']!='1':break
                record=claim(args,f'{socket.gethostname()}:{os.getpid()}')
                if not record:break
                await process(browser,record,args)
                await asyncio.sleep(max(args.delay_ms/1000,1.5))
        finally:
            await browser.close()

def arguments():
    parser=argparse.ArgumentParser(description='PostgreSQL-native Telewebion crawler: metadata only')
    parser.add_argument('--limit',type=int,default=int(os.getenv('TW_BATCH_LIMIT','5')))
    parser.add_argument('--workers',type=int,default=1)
    parser.add_argument('--delay-ms',type=int,default=int(os.getenv('TW_DELAY_MS','1500')))
    parser.add_argument('--max-attempts',type=int,default=int(os.getenv('TW_MAX_ATTEMPTS','3')))
    parser.add_argument('--max-scrolls',type=int,default=int(os.getenv('TW_MAX_SCROLLS','120')))
    parser.add_argument('--max-api-pages',type=int,default=int(os.getenv('TW_MAX_API_PAGES','200')))
    parser.add_argument('--page-size',type=int,default=int(os.getenv('TW_PAGE_SIZE','10')))
    parser.add_argument('--max-seasons',type=int,default=40)
    parser.add_argument('--max-more',type=int,default=120)
    parser.add_argument('--slug',default='')
    parser.add_argument('--kind',choices=('kids','program','product'))
    parser.add_argument('--program',default='')
    parser.add_argument('--retry-partial',action='store_true')
    parser.add_argument('--headed',action='store_true')
    parser.add_argument('--force',action='store_true',help='one-shot manual pilot even when paused')
    args=parser.parse_args()
    if not 1<=args.limit<=100 or args.workers!=1 or args.delay_ms<1500 or args.max_scrolls>200:
        parser.error('limit 1..100, workers=1, delay>=1500ms, max-scrolls<=200')
    return args

def main():
    args=arguments()
    try: asyncio.run(run(args))
    except KeyboardInterrupt: print('[STOPPED] interrupted; incremental results preserved',flush=True)

if __name__=='__main__': main()
