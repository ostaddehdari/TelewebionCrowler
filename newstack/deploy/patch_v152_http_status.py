#!/usr/bin/env python3
"""Targeted, idempotent source repair for NewCrawler 1.5.1 HTTP error classification."""
from pathlib import Path
import sys

path=Path(sys.argv[1]) if len(sys.argv)>1 else Path('/opt/newtwcrawler/newtwcrawler/crawler.py')
s=path.read_text(encoding='utf-8')
if 'V1.5.2: numeric HTTP status classification' in s:
    print('[OK] Already patched:',path)
    raise SystemExit(0)

changes=[(
 'from urllib.parse import urlencode',
 'from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit',
),(
 '''        res=client.get(url,params=params)
        ms=int(1000*(time.monotonic()-start))
        if res.status_code in (401,403,429):
            raise RuntimeError(f'HTTP {res.status_code}: access or rate limit; stop and review')
        res.raise_for_status()''',
 '''        res=client.get(url,params=params)
        # V1.5.2: numeric HTTP status classification; never parse IDs from errors.
        # Follow at most two same-origin HTTPS API redirects, not external hosts.
        for _ in range(2):
            if res.status_code not in (301,302,303,307,308):
                break
            location=res.headers.get('location')
            if not location:
                raise ValueError('Redirect without Location header; manual review')
            target=urljoin(str(res.request.url),location)
            parts=urlsplit(target)
            if (parts.scheme!='https' or parts.netloc!='gateway.telewebion.net'
                    or not parts.path.startswith('/kandoo/')
                    or parts.username or parts.password):
                raise ValueError('Redirect outside approved API origin; manual review')
            # A trailing-slash redirect may omit the original query string.
            old=urlsplit(str(res.request.url))
            if (not parts.query and old.query and
                    parts.path.rstrip('/')==old.path.rstrip('/')):
                target=urlunsplit((parts.scheme,parts.netloc,parts.path,old.query,''))
            res=client.get(target)
        if res.status_code in (301,302,303,307,308):
            raise ValueError('More than two HTTP redirects; manual review')
        ms=int(1000*(time.monotonic()-start))
        if res.status_code == 429:
            raise RuntimeError('HTTP 429: rate limit; global cooldown')
        if res.status_code in (401,403):
            raise RuntimeError(f'HTTP {res.status_code}: access denied for this task')
        res.raise_for_status()''',
),(
 '''            status='review' if 'schema' in msg.lower() or 'non-json' in msg.lower() else ('waiting' if fails<3 else 'error')
            next_delay='6 hours' if status=='review' else ('10 minutes' if status=='waiting' else '24 hours')''',
 '''            http_status=getattr(locals().get('res'), 'status_code',None)
            provider_rate_limited=(http_status==429)
            task_access_denied=(http_status in (401,403))
            manual_review=(task_access_denied or 'schema' in msg.lower()
                 or 'non-json' in msg.lower() or 'redirect' in msg.lower())
            status='review' if manual_review else ('waiting' if fails<3 else 'error')
            next_delay='24 hours' if task_access_denied else (
                '6 hours' if status=='review' else
                ('10 minutes' if status=='waiting' else '24 hours'))''',
),(
 '''               (status,msg,getattr(locals().get('res'), 'status_code',None),next_delay,task['task_key']))
            db.execute(''' + "'''" + '''INSERT INTO crawl_events(task_key,level,message) VALUES(%s,%s,%s)''' + "'''" + ''',(task['task_key'],'throttled' if ('403' in msg or '429' in msg) else 'error',msg))
        print('[ERROR]',typ,msg,flush=True)
        if '403' in msg or '429' in msg:return False''',
 '''               (status,msg,http_status,next_delay,task['task_key']))
            db.execute(''' + "'''" + '''INSERT INTO crawl_events(task_key,level,message) VALUES(%s,%s,%s)''' + "'''" + ''',(task['task_key'],'throttled' if provider_rate_limited else 'error',msg))
        print('[ERROR]',typ,msg,flush=True)
        if provider_rate_limited:return False''',
)]
for old,new in changes:
    count=s.count(old)
    if count!=1:
        raise SystemExit(f'[STOP] Expected one source match, found {count}: {old[:100]!r}')
    s=s.replace(old,new)
compile(s,str(path),'exec')
path.write_text(s,encoding='utf-8')
print('[PATCHED]',path)
