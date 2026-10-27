#!/usr/bin/env python3
"""Idempotent NewCrawler v1.5.3 HTTPS redirect repair (only exact Telewebion API gateways)."""
from __future__ import annotations
from pathlib import Path
import sys

P = Path(sys.argv[1]) if len(sys.argv)>1 else Path('/opt/newtwcrawler/newtwcrawler/crawler.py')
s = P.read_text(encoding='utf-8')
MARKER = '# V1.5.3: exact Telewebion HTTPS gateway redirect allowlist.'
if MARKER in s:
    print('[OK] v1.5.3 already installed:',P)
    sys.exit(0)

changes = [
('''            if (parts.scheme!='https' or parts.netloc!='gateway.telewebion.net'
                    or not parts.path.startswith('/kandoo/')
                    or parts.username or parts.password):
                raise ValueError('Redirect outside approved API origin; manual review')''',
 '''            # V1.5.3: exact Telewebion HTTPS gateway redirect allowlist.
            # Require the same API route and query; never forward to other hosts.
            old=urlsplit(str(res.request.url))
            if (parts.scheme != 'https'
                    or parts.netloc not in ('gateway.telewebion.net','gateway.telewebion.ir')
                    or parts.path.rstrip('/') != old.path.rstrip('/')
                    or not parts.path.startswith('/kandoo/')
                    or parts.fragment or parts.username or parts.password):
                raise ValueError('Redirect outside approved API origin; manual review')
            if not parts.query and old.query:
                target=urlunsplit((parts.scheme,parts.netloc,parts.path,old.query,''))
            elif parts.query != old.query:
                raise ValueError('Redirect unexpectedly changed API query; manual review')'''),
('''        res=client.get(url,params=params)
        # V1.5.2: numeric HTTP status classification''',
 '''        res=client.get(url,params=params)
        # Keep logical request dedupe stable even if actual response comes from .ir.
        original_request_url=str(res.request.url)
        # V1.5.2: numeric HTTP status classification'''),
('''        request_key=hashlib.sha256(str(res.request.url).encode()).hexdigest()''',
 '''        request_key=hashlib.sha256(original_request_url.encode()).hexdigest()'''),
]
# The old v1.5.2 code already includes an identical query-preservation block.
old_existing = '''            # A trailing-slash redirect may omit the original query string.
            old=urlsplit(str(res.request.url))
            if (not parts.query and old.query and
                    parts.path.rstrip('/')==old.path.rstrip('/')):
                target=urlunsplit((parts.scheme,parts.netloc,parts.path,old.query,''))
'''
if old_existing not in s:
    raise SystemExit('[STOP] Expected v1.5.2 query-preservation block absent; no file changed')
s=s.replace(old_existing,'',1)
for old,new in changes:
    if s.count(old)!=1:
        raise SystemExit(f'[STOP] Unexpected crawler source ({s.count(old)} matches): {old[:75]!r}; no file changed')
    s=s.replace(old,new,1)
compile(s,str(P),'exec')
P.write_text(s,encoding='utf-8')
print('[PATCHED] v1.5.3 secure API redirect + stable request deduplication:', P)
