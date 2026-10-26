"""Browser-free, read-only HTML and public API coverage audit for Telewebion."""
from __future__ import annotations
import argparse
from html.parser import HTMLParser
import json
import re
import time
from urllib.parse import urljoin, urlsplit, urlencode
from urllib.request import Request, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError, URLError

HOSTS = {'telewebion.net', 'www.telewebion.net', 'gateway.telewebion.net'}
GATE = 'https://gateway.telewebion.net/kandoo/vod/content/get-kids-serial-season'
LINK = re.compile(r'^/(program|product|kids|episode)/0x[0-9a-f]+/?$', re.I)


def allowed(url):
    try:
        u = urlsplit(url)
        return u.scheme == 'https' and u.hostname in HOSTS and not u.username and not u.password
    except ValueError:
        return False


class Redirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urljoin(req.full_url, newurl)
        if not allowed(target):
            raise ValueError('Non-Telewebion redirect refused')
        return super().redirect_request(req, fp, code, msg, headers, target)


def download(url, referer=''):
    if not allowed(url):
        raise ValueError('Unapproved URL')
    headers = {'User-Agent': 'TelewebionCatalog-HTTP-Audit/1.0',
               'Accept': 'text/html,application/json;q=0.9,*/*;q=0.5',
               'Accept-Encoding': 'identity'}
    if referer and allowed(referer):
        headers['Referer'] = referer
    req = Request(url, headers=headers)
    t = time.monotonic()
    try:
        with build_opener(Redirect()).open(req, timeout=18) as r:
            data = r.read(2_000_001)
            if len(data) > 2_000_000:
                raise ValueError('Body too large, stopped at 2 MB')
            if not allowed(r.geturl()):
                raise ValueError('Unapproved final URL')
            return r.status, r.headers.get('Content-Type', ''), data, time.monotonic()-t
    except HTTPError as err:
        return err.code, err.headers.get('Content-Type', ''), b'', time.monotonic()-t


def children(obj):
    counts = {'episode': 0, 'clip': 0}
    todo = [obj]
    for _ in range(100000):
        if not todo:
            break
        o = todo.pop()
        if isinstance(o, list):
            todo.extend(o[:5000])
        elif isinstance(o, dict):
            typ = str(o.get('content_type') or o.get('type') or '').upper()
            path = urlsplit(str(o.get('episode_url') or o.get('page_url') or o.get('url') or '')).path
            if typ in {'CLIP', 'PROGRAMCLIP', 'PROGRAM_CLIP'}:
                counts['clip'] += 1
            elif typ in {'SERIALPARTS','EPISODE','EPISODES','PROGRAM_EPISODE'} or re.fullmatch(r'/episode/0x[0-9a-f]+/?', path, re.I):
                counts['episode'] += 1
            todo.extend(v for v in o.values() if isinstance(v, (dict, list)))
    return counts


class Parse(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = set()
        self.payloads = []
        self.script = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == 'a' and d.get('href'):
            self.links.add(d['href'])
        if tag == 'script' and (d.get('id') in ('__NEXT_DATA__', '__NUXT_DATA__') or d.get('type') in ('application/json','application/ld+json')):
            self.script = True
            self.parts = []

    def handle_data(self, data):
        if self.script and sum(map(len, self.parts)) < 2_000_000:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.script:
            try:
                self.payloads.append(json.loads(''.join(self.parts)))
            except ValueError:
                pass
            self.parts = []
            self.script = False


def html_metrics(html, url):
    p = Parse()
    p.feed(html)
    paths = {urlsplit(urljoin(url, link)).path.lower()
             for link in p.links if allowed(urljoin(url, link))}
    parent = sum(bool(LINK.fullmatch(x) and not x.startswith('/episode/')) for x in paths)
    episode = sum(bool(re.fullmatch(r'/episode/0x[0-9a-f]+/?', x)) for x in paths)
    nested = {'episode':0, 'clip':0}
    for payload in p.payloads:
        result = children(payload)
        for kind in nested:
            nested[kind] += result[kind]
    return {'parent_links':parent, 'episode_links':episode,
            'embedded_json_scripts':len(p.payloads),
            'json_episode_shapes':nested['episode'],
            'json_clip_shapes':nested['clip'],
            'next_hydration_marker':'__NEXT_DATA__' in html or 'self.__next_f.push' in html}


def samples(n):
    from .db import connect
    programs, channels = [], []
    with connect() as db:
        for kind in ('program','product','kids'):
            programs += [dict(r) for r in db.execute(
                "SELECT program_key,kind,provider_id,url FROM programs WHERE kind=%s AND url LIKE 'https://telewebion.net/%%' ORDER BY program_key LIMIT %s",
                (kind,n))]
        channels = [dict(r) for r in db.execute(
            "SELECT slug,source_url FROM channels WHERE source_url LIKE 'https://telewebion.net/%%' ORDER BY slug LIMIT %s",(n,))]
    return programs, channels


def main():
    p = argparse.ArgumentParser(description='Read-only browserless Telewebion HTML/API probe')
    p.add_argument('--per-kind',type=int,default=1)
    args = p.parse_args()
    if not 1 <= args.per_kind <= 3:
        p.error('--per-kind must be 1..3')
    programs, channels = samples(args.per_kind)
    targets = [(r['kind']+':'+r['program_key'],r['url']) for r in programs]
    targets += [('channel:'+r['slug'],r['source_url']) for r in channels]
    targets += [('archive:'+r['program_key'],r['url'].rstrip('/')+'/archive')
                for r in programs if r['kind']=='program']
    print('[START] Only HTTP requests; no Chromium, no database writes, no media downloads.',flush=True)
    for name, url in targets:
        print('[PAGE]',name,url,flush=True)
        try:
            code, ct, body, seconds = download(url)
            print('RESULT',json.dumps({'http':code,'type':ct.split(';')[0],
                  'bytes':len(body),'seconds':round(seconds,2)},ensure_ascii=False),flush=True)
            if code==200 and 'html' in ct:
                print('HTML',json.dumps(html_metrics(body.decode('utf-8','replace'),url),ensure_ascii=False),flush=True)
        except (ValueError,URLError,OSError,TimeoutError) as exc:
            print('ERROR',type(exc).__name__,str(exc)[:150],flush=True)
        time.sleep(1.5)
    for r in programs:
        if r['kind']!='kids' or not re.fullmatch(r'0x[0-9a-f]+',str(r['provider_id']),re.I):
            continue
        url=GATE+'?'+urlencode({'content_id':r['provider_id'],'season':1,'first':10,'offset':0})
        print('[KIDS API]',r['program_key'],flush=True)
        try:
            code, ct, body, seconds=download(url,referer=r['url'])
            result={'http':code,'content_type':ct.split(';')[0],'bytes':len(body),'seconds':round(seconds,2)}
            if code==200 and body:
                result['child_shapes']=children(json.loads(body))
            print('API',json.dumps(result,ensure_ascii=False),flush=True)
        except (ValueError,URLError,OSError,TimeoutError) as exc:
            print('ERROR',type(exc).__name__,str(exc)[:150],flush=True)
        time.sleep(1.5)
    print('[FINISHED] No database or systemd changes.',flush=True)


if __name__ == '__main__':
    main()
