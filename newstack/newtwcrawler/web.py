from __future__ import annotations
from collections import Counter
from functools import lru_cache
import json
import re
import time
from pathlib import Path
from urllib.parse import quote
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from .db import connect
from .config import BASE_PATH

ROOT=Path(__file__).resolve().parent
app=FastAPI(title='فهرست جامع تلوبیون',docs_url=None,redoc_url=None,openapi_url=None)
app.mount('/static',StaticFiles(directory=str(ROOT/'static')))
tpl=Jinja2Templates(directory=str(ROOT/'templates'))
tpl.env.globals.update(base=BASE_PATH,n=lambda v:f'{int(v or 0):,}',
                       item_url=lambda kind,eid:BASE_PATH+'/item/'+quote(kind,safe='')+'/'+quote(eid,safe=''))
TYPE_NAMES={'channel':'شبکه‌ها','program':'برنامه‌ها','episode':'قسمت‌ها و کلیپ‌ها',
 'content':'فیلم و سریال','tag':'برچسب‌ها','space':'فضاها','page':'صفحات',
 'component':'اجزای صفحه','row':'ردیف‌ها','slider':'اسلایدرها','banner':'بنرها',
 'content_meta':'فراداده‌ها','cast':'عوامل','role':'نقش‌ها'}

def render(request,file,**kwargs):
    return tpl.TemplateResponse(request,file,{'kinds':TYPE_NAMES,**kwargs})

@app.middleware('http')
async def public_headers(req: Request, call_next):
    """Read-only catalog is deliberately public; no auth/session or write actions."""
    response = await call_next(req)
    response.headers['Cache-Control'] = 'private, no-store' if req.url.path in ('/responses',) or req.url.path.startswith('/response/') else 'public, max-age=5'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response

@app.get('/health')
def health():
    try:
        with connect() as c:c.execute('SELECT 1')
        return {'status':'ok'}
    except Exception:raise HTTPException(503)

STATS_CACHE={'at':0,'value':None}
def statistics():
    if STATS_CACHE['value'] is not None and time.monotonic()-STATS_CACHE['at']<8:
        return STATS_CACHE['value']
    with connect() as db:
        types={r['kind']:r['count'] for r in db.execute('SELECT kind,count(*) FROM entities GROUP BY kind')}
        clips=db.execute("SELECT is_clip,count(*) FROM entities WHERE kind='episode' GROUP BY is_clip").fetchall()
        jobs={r['state']:r['count'] for r in db.execute('SELECT state,count(*) FROM crawl_tasks GROUP BY state')}
        raw=db.execute('SELECT COUNT(*) AS n FROM api_responses').fetchone()['n']
        last=db.execute("SELECT max(fetched_at) AS t FROM api_responses").fetchone()['t']
    v={'entities':types,'episodes':next((x['count'] for x in clips if x['is_clip'] is False),0),
       'clips':next((x['count'] for x in clips if x['is_clip'] is True),0),
       'undetermined':next((x['count'] for x in clips if x['is_clip'] is None),0),
       'queue':jobs,'responses':raw,'last':last.isoformat() if last else None}
    STATS_CACHE.update(at=time.monotonic(),value=v)
    return v

@app.get('/',response_class=HTMLResponse)
def index(request:Request,kind:str='',q:str='',page:int=Query(1,ge=1,le=100000)):
    kind=kind if kind in TYPE_NAMES else ''
    q=q.strip()[:90]
    where=['kind=%s'] if kind else []
    args=[kind] if kind else []
    if q:where.append('(title ILIKE %s OR entity_id ILIKE %s)');args.extend([f'%{q}%',f'%{q}%'])
    filt=('WHERE '+' AND '.join(where)) if where else ''
    with connect() as db:
        count=db.execute('SELECT count(*) AS n FROM entities '+filt,args).fetchone()['n']
        rows=db.execute('''SELECT kind,entity_id,title,subtitle,content_type,is_clip,image_ref,updated_at
           FROM entities '''+filt+''' ORDER BY updated_at DESC,kind,entity_id
           LIMIT 32 OFFSET %s''',args+[(page-1)*32]).fetchall()
    return render(request,'index.html',stats=statistics(),items=rows,kind=kind,q=q,
                  page=page,pages=max(1,(count+31)//32),count=count)

@app.get('/item/{kind}/{entity_id}',response_class=HTMLResponse)
def item(request:Request,kind:str,entity_id:str):
    if not re.fullmatch(r'[a-z_]{1,30}',kind) or not re.fullmatch(r'0x[0-9a-fA-F]{1,32}',entity_id):
        raise HTTPException(404)
    with connect() as db:
        row=db.execute('SELECT * FROM entities WHERE kind=%s AND entity_id=%s',(kind,entity_id.lower())).fetchone()
        if not row:raise HTTPException(404)
        links=db.execute('''SELECT l.relation,e.kind,e.entity_id,e.title,e.is_clip FROM links l
         JOIN entities e ON e.kind=l.child_kind AND e.entity_id=l.child_id
         WHERE l.parent_kind=%s AND l.parent_id=%s
         ORDER BY e.kind,e.title LIMIT 160''',(kind,entity_id.lower())).fetchall()
        parent=db.execute('''SELECT e.kind,e.entity_id,e.title FROM links l
          JOIN entities e ON e.kind=l.parent_kind AND e.entity_id=l.parent_id
          WHERE l.child_kind=%s AND l.child_id=%s LIMIT 30''',(kind,entity_id.lower())).fetchall()
    return render(request,'item.html',entry=row,children=links,parents=parent,
                  raw_json=json.dumps(mask(row['raw']),ensure_ascii=False,indent=2))

SECRET_FIELDS={'content_token','authorization','access_token','refresh_token','secret','password','stream_token',
               'cookie','set_cookie','session','client_secret','api_key','apikey','signature'}
def mask(node):
    """Preserve every field name while hiding secret values in this public viewer."""
    if isinstance(node,dict):
        return {k:('[محافظت‌شده؛ در دیتابیس ذخیره شده]' if
                   any(part in k.lower() for part in ('token','secret','password','authorization','cookie','credential'))
                   or k.lower() in SECRET_FIELDS else mask(v)) for k,v in node.items()}
    if isinstance(node,list):return [mask(x) for x in node]
    if isinstance(node,str) and (re.match(r'^eyJ[A-Za-z0-9_-]{12,}\.eyJ',node) or
         re.search(r'[?&](?:token|auth|signature|key)=',node,re.I)):
        return '[محافظت‌شده؛ در دیتابیس ذخیره شده]'
    return node

@app.get('/dashboard',response_class=HTMLResponse)
def dashboard(request:Request,state:str=''):
    state=state if state in ('queued','running','waiting','error','review') else ''
    with connect() as db:
        where='WHERE state=%s' if state else ''
        rows=db.execute('''SELECT task_type,params,cursor_offset,state,fetched_pages,
          discovered_records,last_error,last_http,last_run,updated_at FROM crawl_tasks '''+where+
          ''' ORDER BY CASE state WHEN 'running' THEN 0 WHEN 'error' THEN 1
            WHEN 'review' THEN 2 ELSE 3 END,updated_at DESC LIMIT 100''',[state] if state else []).fetchall()
        events=db.execute('SELECT happened_at,level,message FROM crawl_events ORDER BY id DESC LIMIT 20').fetchall()
    return render(request,'dashboard.html',stats=statistics(),tasks=rows,events=events,state=state)


@app.get('/responses',response_class=HTMLResponse)
def responses(request:Request,endpoint:str='',page:int=Query(1,ge=1,le=100000)):
    endpoint=endpoint.strip()[:50]
    where='WHERE endpoint=%s' if endpoint else ''
    with connect() as db:
        count=db.execute('SELECT count(*) AS n FROM api_responses '+where,[endpoint] if endpoint else []).fetchone()['n']
        rows=db.execute('''SELECT request_key,endpoint,request_params,fetched_at,duration_ms,
            octet_length(payload::text) AS size FROM api_responses '''+where+
            ''' ORDER BY fetched_at DESC LIMIT 35 OFFSET %s''',([endpoint] if endpoint else [])+[(page-1)*35]).fetchall()
    return render(request,'responses.html',entries=rows,endpoint=endpoint,page=page,
                  pages=max(1,(count+34)//35),count=count)

@app.get('/response/{request_key}',response_class=HTMLResponse)
def response_details(request:Request,request_key:str):
    if not re.fullmatch(r'[0-9a-f]{64}',request_key):raise HTTPException(404)
    with connect() as db:
        rec=db.execute('SELECT * FROM api_responses WHERE request_key=%s',(request_key,)).fetchone()
    if not rec:raise HTTPException(404)
    return render(request,'response.html',entry=rec,
           raw_json=json.dumps(mask(rec['payload']),ensure_ascii=False,indent=2))

@app.get('/api/stats')
def stats_api():return statistics()
