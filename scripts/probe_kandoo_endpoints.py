#!/usr/bin/env python3
"""Inspect confirmed Telewebion JSON APIs without Chromium or database writes.

Reports only JSON structure, array sizes, item-field names, and page signatures.
Never logs cookies, tokens, full item payloads or request headers.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import time
from collections import Counter
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = 'https://gateway.telewebion.net'
ALLOWED = 'gateway.telewebion.net'
HEADERS = {
    'Accept': 'application/json, text/plain, */*',
    'Origin': 'https://telewebion.net',
    'Referer': 'https://telewebion.net/',
    'User-Agent': 'TelewebionCatalog/1.0 (+metadata audit)',
}
SENSITIVE = {'authorization', 'cookie', 'password', 'token', 'session', 'signature', 'key'}


def get(path, args):
    url = ROOT + path + '?' + urlencode(args)
    assert urlsplit(url).hostname == ALLOWED
    start = time.monotonic()
    request = Request(url, headers=HEADERS)
    try:
        with urlopen(request, timeout=20) as response:
            if urlsplit(response.geturl()).hostname != ALLOWED:
                raise RuntimeError('unexpected redirect host')
            blob = response.read(4_000_001)
            code = response.status
            typ = response.headers.get('Content-Type', '')
    except HTTPError as error:
        code = error.code
        typ = error.headers.get('Content-Type', '')
        blob = b''
    if len(blob) > 4_000_000:
        raise RuntimeError('response above 4 MB, refused')
    return code, typ, blob, round(time.monotonic() - start, 3)


def shapes(obj, path='$', depth=0, output=None):
    if output is None:
        output = []
    if depth > 8 or len(output) > 90:
        return output
    if isinstance(obj, dict):
        if depth < 3:
            fields = [k for k in obj if not any(w in k.lower() for w in SENSITIVE)]
            output.append({'path': path, 'object_keys': fields[:45]})
        for key, value in list(obj.items())[:90]:
            if isinstance(value, (list, dict)):
                shapes(value, path + '.' + key, depth + 1, output)
    elif isinstance(obj, list):
        fields = []
        if obj and isinstance(obj[0], dict):
            fields = [k for k in obj[0] if not any(w in k.lower() for w in SENSITIVE)]
        output.append({'path': path, 'array_length': len(obj),
                       'first_item_keys': fields[:35]})
        if obj:
            shapes(obj[0], path + '[0]', depth + 1, output)
    return output


def fingerprints(obj):
    """Page-neutral signatures from probable record arrays, never content."""
    found = []
    def walk(value, path='$', depth=0):
        if depth > 8 or len(found) > 100:
            return
        if isinstance(value, dict):
            for k, v in value.items():
                if isinstance(v, (dict, list)):
                    walk(v, path + '.' + k, depth + 1)
        elif isinstance(value, list):
            if value and isinstance(value[0], dict):
                ids = []
                for item in value:
                    if not isinstance(item, dict):
                        continue
                    for key in ('program_id','ProgramID','content_id','episode_id',
                                'clip_id','id','nid','descriptor','program_key'):
                        if item.get(key) is not None:
                            ids.append(str(item[key]))
                            break
                if ids:
                    h = hashlib.sha256(('\\n'.join(ids)).encode()).hexdigest()[:14]
                    found.append({'path':path, 'rows':len(value),
                                  'identified':len(ids), 'page_signature':h})
            if value:
                walk(value[0], path + '[0]', depth + 1)
    walk(obj)
    return found


def probe(label, path, parameters, delay):
    print('\n=== ' + label + ' ===', flush=True)
    print('endpoint=' + path + ' params=' + json.dumps(parameters), flush=True)
    try:
        code, typ, data, duration = get(path, parameters)
        print('HTTP=' + str(code) + ' content_type=' + typ.split(';')[0]
              + ' bytes=' + str(len(data)) + ' seconds=' + str(duration), flush=True)
        if code in (401, 403, 429):
            print('STOP: authentication or rate limit; do not retry automatically', flush=True)
            return False
        if code >= 400 or not data:
            return True
        try:
            parsed = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            print('Not valid JSON; cannot determine schema', flush=True)
            return True
        print('SHAPE=' + json.dumps(shapes(parsed)[:35], ensure_ascii=False), flush=True)
        print('PAGE_IDS=' + json.dumps(fingerprints(parsed)[:20], ensure_ascii=False), flush=True)
    except (HTTPError, URLError, OSError, TimeoutError, ValueError, RuntimeError) as exc:
        print('ERROR=' + type(exc).__name__ + ': ' + str(exc)[:200], flush=True)
        return False
    time.sleep(delay)
    return True


def main():
    ap = argparse.ArgumentParser(description='Inspect Telewebion JSON API pagination')
    ap.add_argument('--program', default='0x10000569')
    ap.add_argument('--channel', default='abadan')
    ap.add_argument('--kids', default='0xc208c07')
    ap.add_argument('--delay', type=float, default=1.5)
    args = ap.parse_args()
    if not 1.5 <= args.delay <= 15:
        ap.error('delay must be at least 1.5 seconds')
    for val in (args.program, args.kids):
        if not val.startswith('0x') or not all(c in '0123456789abcdefABCDEF' for c in val[2:]):
            ap.error('program and kids IDs must be hexadecimal 0x IDs')
    if not args.channel.replace('-', '').replace('_', '').isalnum():
        ap.error('channel descriptor invalid')
    jobs = [
        ('program detail', '/kandoo/program/getProgramPage/', {'ProgramID': args.program}),
        *[(f'channel page offset={i}', '/kandoo/channel/getProgramListByChannel/',
           {'Descriptor':args.channel, 'First':24,'Offset':i}) for i in (0,24,48)],
        *[(f'kids serial offset={i}', '/kandoo/vod/content/get-kids-serial',
           {'content_id':args.kids,'first':10,'offset':i}) for i in (0,10,20)],
        *[(f'kids season offset={i}', '/kandoo/vod/content/get-kids-serial-season',
           {'content_id':args.kids,'season':1,'first':10,'offset':i}) for i in (0,10)],
    ]
    print('[START] Public API metadata only; no Chromium, PostgreSQL writes, or media.')
    for label, path, params in jobs:
        if not probe(label,path,params,args.delay):
            print('[STOP] Probe stopped on connectivity/authorization/rate-limit failure')
            break
    print('[DONE] Review array paths and page signatures to select crawlers safely.')


if __name__ == '__main__':
    main()
