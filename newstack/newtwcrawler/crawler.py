"""Rate-limited single-process HTTP crawler, without Playwright or media downloads."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlencode
import httpx
from .db import connect, init
from .ingest import index_document, doc_hash
from .config import DATA_PATH

API='https://gateway.telewebion.net'
ROUTES={
 'channel_programs':('/kandoo/channel/getProgramListByChannel/', 'First'),
 'program_page':('/kandoo/program/getProgramPage/',''),
 'program_clips':('/kandoo/program/getClipsByProgramID/','First'),
 'tag':('/kandoo/tag/getTagById/',''),
 'tag_episodes':('/kandoo/tag/getEpisodesByTagId/','First'),
 'space':('/kandoo/v8/space/getSpace/','First'),
 'page':('/kandoo/v8/page/getPage/','First'),
 'content':('/kandoo/vod/content/get-content',''),
 'kids_serial':('/kandoo/vod/content/get-kids-serial','first'),
 'kids_season':('/kandoo/vod/content/get-kids-serial-season','first'),
 'serial':('/kandoo/vod/content/get-serial','first'),
 'content_meta':('/kandoo/vod/content-meta/get-content-meta','first'),
}
PAGED_KEYS={
 'channel_programs':('queryChannel','programs'),
 'program_clips':('getClipsByProgramID','episodes'), # Shape checked dynamically below.
 'tag_episodes':('getEpisodesByTagId','episodes'),
 'content_meta':('content_metas','contents'),
 'kids_serial':('content','serial_parts'),
 'kids_season':('content','serial_parts'),
 'serial':('content','serial_parts'),
}
DEFAULT_SIZE={'channel_programs':24,'program_clips':24,'tag_episodes':24,
              'space':10,'page':10,'kids_serial':10,'kids_season':10,'serial':20,'content_meta':48}
HEX=re.compile(r'^0x[0-9a-f]+$',re.I)


def params_key(kind,params):
    packed=json.dumps([kind,params],sort_keys=True,ensure_ascii=False,separators=(',',':'))
    return hashlib.sha256(packed.encode()).hexdigest()[:40]


def add_task(db,kind,params,priority=50):
    if kind not in ROUTES: return
    if any(str(v).startswith('http') or len(str(v))>150 for v in params.values()): return
    task_key=params_key(kind,params)
    db.execute('''INSERT INTO crawl_tasks(task_key,task_type,params,page_size,priority)
     VALUES(%s,%s,%s::jsonb,%s,%s) ON CONFLICT DO NOTHING''',
     (task_key,kind,json.dumps(params,sort_keys=True),DEFAULT_SIZE.get(kind,0),priority))


def bootstrap():
    init()
    seeds=json.loads((DATA_PATH/'channel_seeds.json').read_text(encoding='utf-8'))
    with connect() as db:
        for row in seeds:
            descriptor=row['descriptor']
            data={'ChannelID':row['id'],'descriptor':descriptor,'name':row['name'],'type':row['type']}
            from .ingest import store_entity
            store_entity(db,'channel',row['id'],data)
            add_task(db,'channel_programs',{'Descriptor':descriptor},priority=10)
        for name in ('kids','MovieSeries','sport'):
            add_task(db,'space',{'Name':name},priority=12)
        for name in ('khordsal','wrestling-world-championships-2026','channels_page'):
            add_task(db,'page',{'Name':name},priority=16)
        for tag in ('0x1539cbfa','0x29d0d04'):
            add_task(db,'tag',{'TagId':tag},priority=22)
            add_task(db,'tag_episodes',{'TagId':tag},priority=24)
        add_task(db,'content_meta',{'content_meta_id':'0xc207952'},priority=23)
        for ident in ('0xc20da0b','0xc7f7c34'):
            add_task(db,'content',{'content_id':ident,'q':'abc'},priority=24)
            add_task(db,'kids_serial',{'content_id':ident},priority=24)
            add_task(db,'kids_season',{'content_id':ident,'season':1},priority=24)
    print(f'[BOOTSTRAP] {len(seeds)} channel descriptors and discovery seeds; no old DB imported')


def claim():
    with connect() as db:
        throttle=db.execute("SELECT 1 FROM crawl_events WHERE level='throttled' AND happened_at>now()-interval '1 hour' LIMIT 1").fetchone()
        if throttle:
            print('[HOLD] Provider reported 403/429 recently; honoring 1h cooldown',flush=True)
            return None
        with db.transaction():
            db.execute('''UPDATE crawl_tasks SET state='queued',updated_at=now()
              WHERE state='running' AND last_run<now()-interval '25 minutes' ''')
            row=db.execute('''SELECT * FROM crawl_tasks
              WHERE state IN ('queued','waiting') AND next_run<=now()
              ORDER BY priority ASC,next_run ASC,created_at ASC
              FOR UPDATE SKIP LOCKED LIMIT 1''').fetchone()
            if row:
                db.execute('''UPDATE crawl_tasks SET state='running',last_run=now(),
                 updated_at=now(),attempts=attempts+1 WHERE task_key=%s''',(row['task_key'],))
            return row


def pick_rows(data, task_type):
    body=data.get('body') if isinstance(data,dict) else None
    if not isinstance(body,dict): raise ValueError('Missing body JSON object')
    if task_type=='channel_programs':
        channels=body.get('queryChannel')
        if not isinstance(channels,list):raise ValueError('queryChannel not a list')
        return [p for x in channels if isinstance(x,dict) for p in x.get('programs',[]) if isinstance(p,dict)]
    if task_type=='program_clips':
        # Site can change response wrapper. Prefer explicit clips, then candidate episodes.
        root=body.get('getClipsByProgramID')
        if isinstance(root,list): return root
        if isinstance(root,dict):
            for key in ('clips','episodes','items','results'):
                if isinstance(root.get(key),list): return root[key]
        raise ValueError('Unknown clips JSON schema; raw response saved, requires review')
    if task_type=='tag_episodes':
        obj=body.get('getEpisodesByTagId')
        if not isinstance(obj,dict) or not isinstance(obj.get('episodes'),list): raise ValueError('tag episodes schema changed')
        return obj['episodes']
    if task_type=='content_meta':
        metas=body.get('content_metas')
        if not isinstance(metas,list):raise ValueError('content_metas missing')
        return [i for obj in metas if isinstance(obj,dict) for i in obj.get('contents',[]) if isinstance(i,dict)]
    if task_type in ('kids_serial','kids_season','serial'):
        objs=body.get('content')
        if not isinstance(objs,list):raise ValueError('VOD content missing')
        return [item for obj in objs if isinstance(obj,dict) for item in obj.get('serial_parts',[]) if isinstance(item,dict)]
    if task_type in ('space','page'):
        obj=body.get('getKandooSpaceByName') if task_type=='space' else body.get('getPageByName')
        if not isinstance(obj,dict) or not isinstance(obj.get('rows'),list):raise ValueError('page/space rows missing')
        return obj['rows']
    return None


def enqueue_entities(db, task, payload):
    # Discover only verified identifiers, never scrape arbitrary URLs from untrusted JSON.
    body=payload.get('body',{})
    collected={}
    def walk(x,depth=0):
        if depth>20 or len(collected)>2000:return
        if isinstance(x,dict):
            for field,k in [('ProgramID','program'),('content_id','content'),('ContentID','content'),
                             ('TagID','tag'),('PageID','page'),('SpaceID','space'),('content_meta_id','meta')]:
                v=x.get(field)
                if isinstance(v,str) and HEX.fullmatch(v):
                    collected[(k,v.lower())]=x
            for v in x.values():
                if isinstance(v,(dict,list)):walk(v,depth+1)
        elif isinstance(x,list):
            for y in x:walk(y,depth+1)
    walk(body)
    for (kind, ident),obj in collected.items():
        if kind=='program':
            add_task(db,'program_page',{'ProgramID':ident},priority=30)
            add_task(db,'program_clips',{'ProgramID':ident},priority=35)
        elif kind=='content':
            ctype=str(obj.get('content_type') or '').upper()
            if ctype not in ('SERIALPARTS','EPISODE'):
                add_task(db,'content',{'content_id':ident,'q':'abc'},priority=45)
                if ctype=='SERIAL':
                    add_task(db,'serial',{'content_id':ident,'season':1},priority=50)
                    if task['task_type'] in ('space','page','kids_serial','kids_season'):
                        add_task(db,'kids_serial',{'content_id':ident},priority=50)
                seasons=obj.get('seasons') or obj.get('sorted_seasons') or []
                if isinstance(seasons,list) and task['task_type'] in ('content','kids_serial','kids_season'):
                    for sn in seasons[:40]:
                        if isinstance(sn,int) and 1<=sn<=100:
                            add_task(db,'kids_season',{'content_id':ident,'season':sn},priority=54)
        elif kind=='page' and isinstance(obj.get('name'),str):
            add_task(db,'page',{'Name':obj['name']},priority=33)
        elif kind=='space' and isinstance(obj.get('name'),str):
            add_task(db,'space',{'Name':obj['name']},priority=33)
        elif kind=='tag' and task['task_type'] in ('page','space','tag','tag_episodes'):
            add_task(db,'tag',{'TagId':ident},priority=48)
            add_task(db,'tag_episodes',{'TagId':ident},priority=49)
        elif kind=='meta' and task['task_type']=='content_meta':
            add_task(db,'content_meta',{'content_meta_id':ident},priority=62)


def run_once(client, task, delay):
    typ=task['task_type']; path,pagekey=ROUTES[typ]
    params=dict(task['params']); offset=task['cursor_offset']; size=task['page_size']
    if pagekey:
        params[pagekey]=size
        params['Offset' if pagekey=='First' else 'offset']=offset
    url=API+path
    start=time.monotonic()
    try:
        res=client.get(url,params=params)
        ms=int(1000*(time.monotonic()-start))
        if res.status_code in (401,403,429):
            raise RuntimeError(f'HTTP {res.status_code}: access or rate limit; stop and review')
        res.raise_for_status()
        if 'json' not in res.headers.get('content-type','').lower(): raise ValueError('Non-JSON response')
        if len(res.content)>8_000_000: raise ValueError('Response exceeds 8 MB')
        doc=res.json()
        if not isinstance(doc,dict) or doc.get('message') not in (None,'success'):
            raise ValueError('Unexpected API error object')
        try:
            rows=pick_rows(doc,typ)
            schema_error=None
        except (ValueError,KeyError,TypeError) as exc:
            rows=None
            schema_error=str(exc)
        if rows is not None:
            if not isinstance(rows,list): raise ValueError('Rows not a list')
            ids=[]
            for obj in rows:
                if isinstance(obj,dict):
                    ids.append(str(obj.get('ProgramID') or obj.get('EpisodeID') or
                              obj.get('ContentID') or obj.get('content_id') or
                              obj.get('RowID') or obj.get('id') or ''))
            signature=hashlib.sha256(json.dumps(ids,sort_keys=True).encode()).hexdigest()[:24]
        else:signature=None
        request_key=hashlib.sha256(str(res.request.url).encode()).hexdigest()
        with connect() as db:
            with db.transaction():
                db.execute('''INSERT INTO api_responses(request_key,endpoint,request_params,url,payload,payload_sha256,duration_ms)
                  VALUES(%s,%s,%s::jsonb,%s,%s::jsonb,%s,%s) ON CONFLICT(request_key) DO UPDATE SET
                  payload=EXCLUDED.payload,payload_sha256=EXCLUDED.payload_sha256,
                  fetched_at=now(),duration_ms=EXCLUDED.duration_ms''',
                  (request_key,typ,json.dumps(params),str(res.request.url),json.dumps(doc,ensure_ascii=False),doc_hash(doc),ms))
                counts,_=index_document(db,doc,task_type=typ,channel=params.get('Descriptor'))
                enqueue_entities(db,task,doc)
                # Stop on empty, short page, or previously seen page. Never loop endlessly.
                if not pagekey:
                    more=False
                elif signature==task.get('last_signature'):
                    more=False
                elif rows is None:
                    more=False
                else:
                    more=len(rows)>=size and len(rows)>0 and offset//size < 500
                total=sum(counts.values())
                if schema_error:
                    next_offset=offset
                    state='review';wait='24 hours'
                elif more:
                    next_offset=offset+size
                    state='queued'; wait='0 seconds'
                else:
                    next_offset=0
                    state='waiting'; wait='24 hours'
                db.execute('''UPDATE crawl_tasks SET state=%s,cursor_offset=%s,
                   last_signature=%s,attempts=0,fetched_pages=fetched_pages+1,
                   discovered_records=discovered_records+%s,last_http=%s,
                   next_run=now()+(%s)::interval,last_error=%s,updated_at=now() WHERE task_key=%s''',
                   (state,next_offset,signature if more else None,total,res.status_code,wait,schema_error,task['task_key']))
                db.execute('''INSERT INTO crawl_events(task_key,level,message) VALUES(%s,'info',%s)''',
                    (task['task_key'],f'{typ}: offset={offset}; {len(rows) if rows is not None else "detail"} rows; {total} indexed; {ms} ms'))
        print(f'[OK] {typ} offset={offset} rows={len(rows) if rows is not None else "detail"} indexed={total} {ms}ms' + (f' [REVIEW] {schema_error}' if schema_error else ''),flush=True)
        return True
    except (httpx.HTTPError,RuntimeError,ValueError,KeyError,TypeError) as e:
        msg=f'{type(e).__name__}: {str(e)[:350]}'
        with connect() as db:
            fails=task['attempts']+1
            status='review' if 'schema' in msg.lower() or 'non-json' in msg.lower() else ('waiting' if fails<3 else 'error')
            next_delay='6 hours' if status=='review' else ('10 minutes' if status=='waiting' else '24 hours')
            db.execute('''UPDATE crawl_tasks SET state=%s,last_error=%s,last_http=%s,
               next_run=now()+(%s)::interval,updated_at=now() WHERE task_key=%s''',
               (status,msg,getattr(locals().get('res'), 'status_code',None),next_delay,task['task_key']))
            db.execute('''INSERT INTO crawl_events(task_key,level,message) VALUES(%s,%s,%s)''',(task['task_key'],'throttled' if ('403' in msg or '429' in msg) else 'error',msg))
        print('[ERROR]',typ,msg,flush=True)
        if '403' in msg or '429' in msg:return False
        return True


def main():
    ap=argparse.ArgumentParser()
    sub=ap.add_subparsers(dest='action',required=True)
    sub.add_parser('bootstrap')
    run=sub.add_parser('run')
    run.add_argument('--max-requests',type=int,default=25)
    run.add_argument('--delay',type=float,default=1.5)
    args=ap.parse_args()
    if args.action=='bootstrap':bootstrap();return
    if not 1.0<=args.delay<=30 or not 1<=args.max_requests<=1000:ap.error('Bad safety limits')
    init()
    headers={'Accept':'application/json, text/plain, */*',
             'Origin':'https://telewebion.net','Referer':'https://telewebion.net/',
             'User-Agent':'TelewebionCatalog/1.0 (metadata only)'}
    with httpx.Client(timeout=25,follow_redirects=False,headers=headers,
                      limits=httpx.Limits(max_connections=2),http2=True) as client:
        processed=0
        while processed<args.max_requests:
            job=claim()
            if not job:
                print('[IDLE] No currently due tasks');break
            if not run_once(client,job,args.delay):break
            processed+=1
            time.sleep(args.delay)
    print('[BATCH DONE] requests:',processed)

if __name__=='__main__':main()
