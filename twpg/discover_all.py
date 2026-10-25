"""Discover public program parents across imported channel source URLs.

Single browser at a time; no video downloads and no bypass of website limits.
The channel crawl checkpoint is committed only after a successful scan.
"""
from __future__ import annotations
import argparse
import asyncio
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit
from .db import connect, event, initialize
from .discover import SOURCE_MAP, browse, save_results

ALLOWED_HOSTS={'telewebion.net','www.telewebion.net'}


def source_url(row):
    # Imported source_url takes precedence over any outdated default aliases.
    link=row['source_url'] or SOURCE_MAP.get(row['slug']) or ''
    try:
        parsed=urlsplit(link)
        if parsed.scheme=='https' and parsed.hostname in ALLOWED_HOSTS and not parsed.username and not parsed.password:
            return link
    except ValueError:
        pass
    return None


def is_due(value, hours, now=None):
    if not value or hours<=0:return True
    now=now or datetime.now(timezone.utc)
    try:
        last=datetime.fromisoformat(value)
        if last.tzinfo is None:return True
        return last <= now-timedelta(hours=hours)
    except (TypeError,ValueError):
        return True


def rows_due(channel_slug, kind, min_age_hours):
    with connect() as db:
        query='SELECT c.slug,c.name,c.kind,c.source_url,s.value AS last_seen FROM channels c LEFT JOIN settings s ON s.key=CONCAT(\'discovery.last.\',c.slug)'
        filters=[];args=[]
        if channel_slug:filters.append('c.slug=%s');args.append(channel_slug)
        if kind:filters.append('c.kind=%s');args.append(kind)
        if filters:query+=' WHERE '+' AND '.join(filters)
        query+=' ORDER BY CASE WHEN c.kind=\'tv\' THEN 0 ELSE 1 END,c.slug'
        rows=db.execute(query,args).fetchall()
    return [r for r in rows if is_due(r['last_seen'],min_age_hours)]


def record_discovery(slug, observed):
    with connect() as db:
        now=datetime.now(timezone.utc).isoformat()
        for key,value in [(f'discovery.last.{slug}',now),(f'discovery.observed.{slug}',str(observed))]:
            db.execute('INSERT INTO settings(key,value) VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value',(key,value))
        event(db,'info',f'Discovered channel {slug}: {observed} parents observed')


def arguments():
    parser=argparse.ArgumentParser(description='Discover newly listed Telewebion programs across channels')
    parser.add_argument('--slug',default='',help='scan one channel only')
    parser.add_argument('--kind',choices=('tv','virtual'),default=None)
    parser.add_argument('--all',action='store_true',help='process all due channels, instead of pilot limit')
    parser.add_argument('--limit',type=int,default=2)
    parser.add_argument('--scrolls',type=int,default=20)
    parser.add_argument('--delay-ms',type=int,default=1500)
    parser.add_argument('--gap-seconds',type=int,default=15)
    parser.add_argument('--min-age-hours',type=int,default=24)
    parser.add_argument('--max-errors',type=int,default=3)
    args=parser.parse_args()
    if args.limit<1 or args.scrolls<1 or args.scrolls>120 or args.delay_ms<1500 or args.gap_seconds<10 or args.min_age_hours<0 or args.max_errors<1:
        parser.error('invalid safety limits: scrolls 1..120, delay>=1500, gap>=10, min-age>=0')
    return args


def main():
    args=arguments()
    initialize()
    batch=rows_due(args.slug,args.kind,args.min_age_hours)
    batch=batch if args.all or args.slug else batch[:args.limit]
    if not batch:
        print('[DISCOVERY] No channels due; use --min-age-hours 0 for a manual rescan',flush=True)
        return
    print(f'[DISCOVERY] due={len(batch)} scrolls={args.scrolls} gap={args.gap_seconds}s',flush=True)
    errors=0
    for pos,row in enumerate(batch,1):
        slug=row['slug'];link=source_url(row)
        if not link:
            print(f'[SKIP] {slug}: invalid/unavailable official URL',flush=True)
            continue
        try:
            records=asyncio.run(browse(link,args.scrolls,args.delay_ms))
            observed=save_results(slug,row['name'],link,records)
            record_discovery(slug,observed)
            print(f'[OK] {pos}/{len(batch)} {slug}: observed={observed}',flush=True)
        except (KeyboardInterrupt,SystemExit):
            raise
        except Exception as e:
            errors+=1
            message=f'{type(e).__name__}: {e}'
            print(f'[ERROR] {slug}: {message}',flush=True)
            with connect() as db:
                event(db,'error',f'Channel discovery {slug}: {message[:500]}')
            if 'HTTP 429' in message or 'HTTP 403' in message or errors>=args.max_errors:
                print('[STOP] Throttling/access errors or maximum errors reached. Check logs.',flush=True)
                raise SystemExit(2)
        if pos<len(batch):
            import time
            time.sleep(args.gap_seconds)
    print('[DISCOVERY DONE]',flush=True)

if __name__=='__main__':main()
