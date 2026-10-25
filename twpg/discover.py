"""Resumable parent catalog discovery directly to PostgreSQL.

Uses the proven DOM rules from original V4 crawler, not HTML regex heuristics.
Run manually for a source/network after testing current website markup.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright
from .db import connect,event,initialize
from .scraper import launch
from .utils import utc

ROOT=Path(__file__).resolve().parent
DOM=(ROOT/'dom.js').read_text(encoding='utf-8')
SCROLL=(ROOT/'scroll.js').read_text(encoding='utf-8')
SOURCE_MAP={'tw-series':'https://telewebion.net/space/MovieSeries',
            'tw-sport':'https://telewebion.net/space/sport',
            'tw-kids':'https://telewebion.net/kids',
            'tw-movies':'https://telewebion.net/page/movie'}


def normalize(raw):
    """Do not treat episode page or arbitrary external links as catalog parents."""
    if not isinstance(raw,dict):return None
    url=raw.get('url') or ''
    try:u=urlsplit(url)
    except ValueError:return None
    if u.scheme!='https' or u.hostname not in ('telewebion.net','www.telewebion.net'):return None
    match=re.fullmatch(r'/(program|product|kids)/(0x[0-9a-fA-F]+)',u.path.rstrip('/'))
    if not match:return None
    title=re.sub(r'\s+',' ',str(raw.get('name') or '').strip())
    if not title or len(title)>240:return None
    kind,ident=match.groups();kind=kind.lower();ident=ident.lower()
    return {'program_key':kind+':'+ident,'provider_id':ident,'kind':kind,'name':title,
            'url':f'https://telewebion.net/{kind}/{ident}',
            'image_url':raw.get('image_url') or None,'raw_json':json.dumps(raw,ensure_ascii=False,separators=(',',':'))}

def save_results(slug:str,name:str,url:str,results:list[dict]):
    added=0
    with connect() as db:
        db.execute('''INSERT INTO channels(slug,name,kind,source_url,created_at) VALUES(%s,%s,%s,%s,%s)
          ON CONFLICT(slug) DO NOTHING''',(slug,name,'tv' if not slug.startswith('tw-') else 'virtual',url,utc()))
        for raw in results:
            rec=normalize(raw)
            if rec is None:continue
            db.execute('''INSERT INTO programs(program_key,provider_id,kind,name,url,image_url,raw_json,updated_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(program_key) DO UPDATE SET
               name=EXCLUDED.name,url=EXCLUDED.url,
               image_url=COALESCE(EXCLUDED.image_url,programs.image_url),
               updated_at=EXCLUDED.updated_at''',
               (rec['program_key'],rec['provider_id'],rec['kind'],rec['name'],rec['url'],
                rec['image_url'],rec['raw_json'],utc()))
            db.execute('''INSERT INTO program_channels(program_key,slug) VALUES(%s,%s)
               ON CONFLICT DO NOTHING''',(rec['program_key'],slug))
            db.execute('''INSERT INTO jobs(program_key,status,updated_at) VALUES(%s,'pending',%s)
               ON CONFLICT DO NOTHING''',(rec['program_key'],utc()))
            added+=1
        event(db,'info',f'Parent discovery {slug}: {added} observed records',None,url)
    return added

async def browse(url:str,scrolls:int,delay_ms:int):
    seen={}
    async with async_playwright() as playwright:
        browser=await launch(playwright)
        context=await browser.new_context(locale='fa-IR',viewport={'width':1450,'height':950},service_workers='block')
        async def block_media(route):
            pathname=urlsplit(route.request.url).path.lower()
            if pathname.endswith(('.mp4','.m3u8','.mpd','.ts','.webm')):
                await route.abort()
            else:
                await route.continue_()
        await context.route('**/*',block_media)
        page=await context.new_page()
        try:
            resp=await page.goto(url,wait_until='domcontentloaded',timeout=60000)
            if resp and resp.status>=400:raise RuntimeError(f'HTTP {resp.status} for {url}')
            await page.wait_for_timeout(1000)
            no_gain=0;no_move=0
            for i in range(scrolls):
                state=await page.evaluate(DOM)
                previous=len(seen)
                for raw in state.get('items',[]):
                    record=normalize(raw)
                    if record:seen[record['program_key']]=raw
                no_gain=no_gain+1 if previous==len(seen) else 0
                moved=await page.evaluate(SCROLL)
                no_move=no_move+1 if not moved['moved'] else 0
                if i%20==0:print('[PARENTS] scroll:',i,'unique:',len(seen),flush=True)
                if no_gain>=10 and no_move>=6:break
                await page.wait_for_timeout(delay_ms)
        finally:
            await context.close()
            await browser.close()
    return list(seen.values())

def main():
    ap=argparse.ArgumentParser(description='Discover new parent shows for one Telewebion network')
    ap.add_argument('--slug',required=True,help='e.g. tv1,tw-series,tw-kids')
    ap.add_argument('--url',default='',help='override source URL, must be telewebion.net HTTPS')
    ap.add_argument('--scrolls',type=int,default=80)
    ap.add_argument('--delay-ms',type=int,default=1500)
    args=ap.parse_args()
    if not re.fullmatch(r'[a-z0-9_-]{1,70}',args.slug):ap.error('invalid slug')
    if not 1<=args.scrolls<=200 or args.delay_ms<1500:ap.error('scrolls 1..200; delay-ms >=1500')
    initialize()
    with connect() as db:
        network=db.execute('SELECT name,source_url FROM channels WHERE slug=%s',(args.slug,)).fetchone()
    url=args.url or SOURCE_MAP.get(args.slug) or (network['source_url'] if network else None)
    name=(network['name'] if network else args.slug)
    parsed=urlsplit(url or '')
    if parsed.scheme!='https' or parsed.hostname not in ('telewebion.net','www.telewebion.net'):
        ap.error('Only official telewebion.net HTTPS sources allowed')
    print('[DISCOVER]',args.slug,url,flush=True)
    results=asyncio.run(browse(url,args.scrolls,args.delay_ms))
    print('[FOUND]',len(results),flush=True)
    print('[STORED]',save_results(args.slug,name,url,results),flush=True)

if __name__=='__main__':main()
