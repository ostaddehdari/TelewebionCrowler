"""Browser-free, conservative clip import from Telewebion Kandoo.

Dry-run by default. Writes clips only with --save, preserving existing jobs/status
and never downloading video. All requests are HTTP GET to the known gateway.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError, URLError

ENDPOINT = 'https://gateway.telewebion.net/kandoo/program/getClipsByProgramID/'
VALID_ID = re.compile(r'0x[0-9a-f]+', re.I)
ID_FIELDS = ('content_id', 'clip_id', 'episode_id', 'id', 'nid', 'ContentID', 'ContentId', 'Id')
TITLE_FIELDS = ('special_title', 'clip_title', 'episode_title', 'title', 'persian_title', 'name',
                'Title', 'Name')
PARENT_TYPES = {'SERIAL', 'MOVIE', 'PRODUCT', 'PROGRAM', 'KIDS', 'SERIES', 'CHANNEL'}
MEDIA_HINTS = ('clip', 'content', 'items', 'result', 'data')


class SchemaError(Exception):
    pass


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urlsplit(newurl)
        if u.scheme != 'https' or u.hostname != 'gateway.telewebion.net':
            raise RuntimeError('Redirected outside approved Telewebion gateway')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def request_page(program_id: str, first: int, offset: int):
    url = ENDPOINT + '?' + urlencode({'ProgramID': program_id, 'First': first, 'Offset': offset})
    req = Request(url, headers={
        'Accept': 'application/json, text/plain, */*',
        'Origin': 'https://telewebion.net',
        'Referer': 'https://telewebion.net/',
        'User-Agent': 'TelewebionCatalog/1.0 (metadata only)',
        'Accept-Encoding': 'identity',
    })
    try:
        with build_opener(SafeRedirect()).open(req, timeout=25) as resp:
            if resp.status != 200:
                raise RuntimeError(f'HTTP {resp.status}')
            if 'json' not in resp.headers.get('Content-Type', '').lower():
                raise SchemaError('Endpoint returned non-JSON data')
            data = resp.read(4_000_001)
            if len(data) > 4_000_000:
                raise RuntimeError('JSON response exceeds size limit')
        return json.loads(data)
    except HTTPError as exc:
        if exc.code in (401, 403, 429):
            raise RuntimeError(f'HTTP {exc.code}: access/rate-limit; stopping') from exc
        raise RuntimeError(f'HTTP {exc.code}: endpoint failure') from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f'Network error: {type(exc).__name__}: {exc}') from exc


def identity(row: dict):
    for field in ID_FIELDS:
        value = row.get(field)
        if value is not None and VALID_ID.fullmatch(str(value)):
            return str(value).lower()
    return None


def title_of(row: dict):
    for field in TITLE_FIELDS:
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()[:350]
    return None


def valid_clip(row):
    if not isinstance(row, dict):
        return False
    typ = str(row.get('content_type') or row.get('type') or row.get('ContentType') or '').upper()
    if typ in PARENT_TYPES:
        return False
    return bool(identity(row) and title_of(row))


def candidate_lists(doc, prefix=(), depth=0):
    if depth > 7:
        return
    if isinstance(doc, dict):
        for name, value in list(doc.items())[:100]:
            if isinstance(value, (dict, list)):
                yield from candidate_lists(value, prefix + (name,), depth+1)
    elif isinstance(doc, list):
        # The list itself might be a collection of clips.
        if all(isinstance(x, dict) for x in doc[:6]):
            good = sum(valid_clip(x) for x in doc[:50])
            if good:
                yield prefix, doc, good
        # Some APIs wrap arrays in nested arrays, but don't scan every record.
        for i, value in enumerate(doc[:2]):
            if isinstance(value, (dict, list)):
                yield from candidate_lists(value, prefix + (i,), depth+1)


def locate(doc, path):
    cursor = doc
    for part in path:
        cursor = cursor[part]
    if not isinstance(cursor, list):
        raise SchemaError('Array path unexpectedly changed')
    return cursor


def find_clip_array(doc):
    candidates = list(candidate_lists(doc))
    if not candidates:
        raise SchemaError('Could not locate a clip array with 0x IDs and titles')
    # Prefer the largest valid list and a meaningful list path.
    def ranking(candidate):
        path, items, good = candidate
        semantic = any(any(h in str(part).lower() for h in MEDIA_HINTS) for part in path)
        return (good, semantic, len(items))
    ordered = sorted(candidates, key=ranking, reverse=True)
    if len(ordered) > 1 and ranking(ordered[0]) == ranking(ordered[1]):
        raise SchemaError('Ambiguous response: two candidate arrays; inspect schema first')
    path, rows, _ = ordered[0]
    return path, rows


def normalize_clip(row, program_key):
    clip_id = identity(row)
    title = title_of(row)
    if not clip_id or not title:
        return None
    # Don't fabricate source URLs; normalize only trusted known episode URLs.
    raw_url = row.get('episode_url') or row.get('page_url') or row.get('web_url') or row.get('url')
    source_url = None
    if isinstance(raw_url, str):
        u = urlsplit(raw_url)
        if u.scheme == 'https' and u.hostname in ('telewebion.net', 'www.telewebion.net') \
                and re.fullmatch(r'/episode/0x[0-9a-f]+/?', u.path, re.I):
            source_url = raw_url
    image = row.get('image_url') or row.get('thumbnail')
    if not isinstance(image, str) or not image.startswith('https://'):
        image = None
    return {'provider_id':clip_id, 'program_key':program_key, 'title':title,
            'source_url':source_url, 'image_url':image, 'raw':row}


def save_batch(program_key, clips):
    """Idempotent PG upsert. Never changes job status or deletes episodes."""
    from .db import connect
    from .utils import utc
    with connect() as db:
        with db.transaction():
            program = db.execute('SELECT 1 FROM programs WHERE program_key=%s', (program_key,)).fetchone()
            if not program:
                raise RuntimeError('Unknown program in PostgreSQL: '+program_key)
            for clip in clips:
                existing = db.execute(
                    "SELECT item_key FROM media_items WHERE program_key=%s AND kind='clip' AND provider_id=%s LIMIT 1",
                    (program_key, clip['provider_id'])).fetchone()
                unique = f"{program_key}|clip|id:{clip['provider_id']}"
                key = existing['item_key'] if existing else hashlib.sha256(unique.encode('utf-8')).hexdigest()[:32]
                now = utc()
                db.execute("""
                    INSERT INTO media_items
                    (item_key,program_key,kind,provider_id,title,source_url,image_url,raw_json,
                     source_type,first_seen,last_seen)
                    VALUES (%s,%s,'clip',%s,%s,%s,%s,%s,'kandoo_clips_api',%s,%s)
                    ON CONFLICT(item_key) DO UPDATE SET
                    title=EXCLUDED.title,
                    source_url=COALESCE(EXCLUDED.source_url, media_items.source_url),
                    image_url=COALESCE(EXCLUDED.image_url, media_items.image_url),
                    raw_json=EXCLUDED.raw_json,
                    source_type=EXCLUDED.source_type,
                    last_seen=EXCLUDED.last_seen
                """, (key, program_key, clip['provider_id'], clip['title'],
                      clip['source_url'],clip['image_url'],
                      json.dumps(clip['raw'],ensure_ascii=False,separators=(',',':')),now,now))
            db.execute("""
                UPDATE jobs SET clips_found=(
                    SELECT COUNT(*) FROM media_items WHERE program_key=%s AND kind='clip'
                ),updated_at=%s WHERE program_key=%s
            """, (program_key, utc(), program_key))


def main():
    p = argparse.ArgumentParser(description='Conservative browser-free Telewebion clips crawler')
    p.add_argument('--program-id',required=True)
    p.add_argument('--first',type=int,default=24)
    p.add_argument('--max-pages',type=int,default=3)
    p.add_argument('--delay',type=float,default=1.5)
    p.add_argument('--save',action='store_true',help='Upsert valid clips to PostgreSQL')
    args = p.parse_args()
    if not VALID_ID.fullmatch(args.program_id):
        p.error('--program-id must look like 0x16b69aa2')
    if not 1 <= args.first <= 50 or not 1 <= args.max_pages <= 500 or not 1.5 <= args.delay <= 30:
        p.error('first=1..50, max-pages=1..500, delay=1.5..30 seconds')
    key = 'program:' + args.program_id.lower()
    print(f'[START] {key} first={args.first} max_pages={args.max_pages} save={args.save}',flush=True)
    path = None
    seen_ids = set()
    for page in range(args.max_pages):
        offset = page * args.first
        try:
            doc = request_page(args.program_id,args.first,offset)
            if path is None:
                path, rows = find_clip_array(doc)
                print('[SCHEMA] clip_array=' + '.'.join(map(str,path)),flush=True)
            else:
                rows = locate(doc,path)
            if not rows:
                print('[END] empty page offset='+str(offset),flush=True)
                break
            if any(not isinstance(x,dict) for x in rows):
                raise SchemaError('Non-object elements in clip array')
            clips = [x for x in (normalize_clip(r,key) for r in rows) if x]
            if not clips:
                raise SchemaError('Nonempty page, but no valid clip ID/title pairs')
            new = [x for x in clips if x['provider_id'] not in seen_ids]
            if not new:
                print('[END] all IDs repeated at offset='+str(offset),flush=True)
                break
            print(f'[PAGE] offset={offset} rows={len(rows)} recognized={len(clips)} new={len(new)}',flush=True)
            if args.save:
                save_batch(key,new)
                print(f'[SAVED] {len(new)} valid clip records',flush=True)
            seen_ids.update(x['provider_id'] for x in new)
            if len(rows) < args.first:
                print('[END] short last page',flush=True)
                break
            if page+1 < args.max_pages:
                time.sleep(args.delay)
        except (SchemaError,RuntimeError,ValueError,KeyError,IndexError) as exc:
            print('[STOP] '+type(exc).__name__+': '+str(exc)[:230],flush=True)
            raise SystemExit(2)
    print(f'[DONE] unique_clip_ids={len(seen_ids)} write={args.save}',flush=True)
    if not args.save:
        print('[DRY RUN] No database changes. Use --save only after validating page counts.',flush=True)


if __name__ == '__main__':
    main()
