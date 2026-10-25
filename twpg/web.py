"""Private Telewebion catalog: RTL UI, login session, live stats, channel jobs."""
from __future__ import annotations
import math
import os
import re
import time
import threading
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from .db import connect, event, stats
from .channel_stats import all_channels
from .auth import COOKIE, MAX_AGE, create_session, read_session, valid_password, configured

BASE = os.getenv('TW_BASE_PATH', '/twcrawler').rstrip('/')
assert BASE.startswith('/') and '//' not in BASE and '..' not in BASE
app = FastAPI(title='سامانه استخراج تلوبیون', docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(Path(__file__).with_name('templates')))
PAGE_SIZE = 30
_FAILED_LOGINS: dict[str, list[float]] = {}
_LOGIN_LOCK = threading.Lock()

def too_many_logins(ip: str, register: bool = False) -> bool:
    now = time.monotonic()
    with _LOGIN_LOCK:
        # Local per-process cap: max 6 failed attempts per 5-minute window.
        if len(_FAILED_LOGINS) > 2048:
            for key in list(_FAILED_LOGINS)[:1024]: _FAILED_LOGINS.pop(key, None)
        times = [t for t in _FAILED_LOGINS.get(ip, []) if t > now - 300]
        if register: times.append(now)
        _FAILED_LOGINS[ip] = times
        return len(times) >= 6
STATUS_LABELS = {'pending':'در انتظار','running':'در حال استخراج','partial':'ناقص','done':'تکمیل‌شده',
                 'failed':'خطادار','empty_review':'نیازمند بررسی'}
templates.env.globals.update(base=BASE, status_label=lambda key: STATUS_LABELS.get(key, key or 'نامشخص'),
                             num=lambda n: format(int(n or 0), ','))
app.mount('/static', StaticFiles(directory=str(Path(__file__).with_name('static'))), name='static')

# Deny by default, including API endpoints. Health is only for monitoring; no data exposed.
@app.middleware('http')
async def auth_gate(request: Request, call_next):
    path = request.url.path
    if path == '/health' or path.startswith('/static/') or path == '/login':
        response = await call_next(request)
    elif not configured():
        response = JSONResponse({'detail':'پنل هنوز پیکربندی امنیتی نشده است'}, status_code=503)
    elif not read_session(request.cookies.get(COOKIE)):
        if path.startswith('/api/') or request.method != 'GET':
            response = JSONResponse({'detail':'ورود الزامی است'}, status_code=401)
        else:
            response = RedirectResponse(f'{BASE}/login', status_code=303)
    else:
        response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    return response

def render(request: Request, name: str, context: dict):
    return templates.TemplateResponse(request, name, context)

@app.get('/login', response_class=HTMLResponse)
def login_form(request:Request):
    if read_session(request.cookies.get(COOKIE)):
        return RedirectResponse(BASE + '/dashboard', status_code=303)
    return render(request, 'login.html', {'error':None, 'ready':configured()})

@app.post('/login', response_class=HTMLResponse)
def login(request:Request, username:str=Form(''), password:str=Form('')):
    # Avoid credential exposure in URLs. No external redirects and a password hash is compared.
    username=username.strip()[:100]
    ip=request.headers.get('x-real-ip') or (request.client.host if request.client else 'unknown')
    if too_many_logins(ip):
        return templates.TemplateResponse(request, 'login.html', {'error':'تلاش‌های ورود بیش از حد است؛ چند دقیقه دیگر دوباره امتحان کنید.','ready':True}, status_code=429)
    if not configured():
        return render(request,'login.html',{'error':'ورود هنوز پیکربندی نشده است.','ready':False})
    ok = (username == os.getenv('TWC_UI_USER','manager') and
          valid_password(password,os.getenv('TWC_UI_PASSWORD_HASH','')))
    if not ok:
        too_many_logins(ip,register=True)
        return templates.TemplateResponse(request, 'login.html', {'error':'نام کاربری یا رمز عبور صحیح نیست.','ready':True}, status_code=401)
    with _LOGIN_LOCK:
        _FAILED_LOGINS.pop(ip,None)
    response=RedirectResponse(BASE+'/dashboard',status_code=303)
    response.set_cookie(COOKIE,create_session(username),max_age=MAX_AGE,httponly=True,
                        secure=True,samesite='strict',path=BASE+'/')
    return response

@app.post('/logout')
def logout(request:Request):
    response=RedirectResponse(BASE+'/login',status_code=303)
    response.delete_cookie(COOKIE,path=BASE+'/')
    return response

@app.get('/health')
def health():
    try:
        with connect() as c: c.execute('SELECT 1')
        return {'status':'ok','database':'postgresql'}
    except Exception: raise HTTPException(503,detail='database unavailable')

def totals(c):
    s=stats(c)
    s['episodes']=s['media_kinds'].get('episode',0)
    s['clips']=s['media_kinds'].get('clip',0)
    s['unresolved']=sum(s['job_states'].get(k,0) for k in ('pending','partial','failed','empty_review','running'))
    return s

def recent_jobs(c, q='', channel='', status='', limit=80):
    where=[]; args=[]
    if q:
        where.append('(p.name ILIKE %s OR p.program_key ILIKE %s OR c.name ILIKE %s)')
        args.extend(['%'+q+'%']*3)
    if channel:
        where.append('EXISTS (SELECT 1 FROM program_channels pcf WHERE pcf.program_key=j.program_key AND pcf.slug=%s)')
        args.append(channel)
    if status in STATUS_LABELS:
        where.append('j.status=%s');args.append(status)
    # Show actionable activity by default. Filtering/search expands to all rows.
    if not (q or channel or status):
        where.append("j.status IN ('running','partial','failed','pending','empty_review')")
    where_sql=('WHERE '+' AND '.join(where)) if where else ''
    query='''SELECT j.program_key,p.name,j.status,j.attempts,j.updated_at,j.started_at,
      j.episodes_found,j.clips_found,j.last_error,
      COALESCE(string_agg(DISTINCT c.name,'، '),'بدون شبکه') AS networks,
      COALESCE(string_agg(DISTINCT c.slug,','),'') AS slugs
      FROM jobs j JOIN programs p ON p.program_key=j.program_key
      LEFT JOIN program_channels pc ON pc.program_key=p.program_key
      LEFT JOIN channels c ON c.slug=pc.slug
      '''+where_sql+''' GROUP BY j.program_key,p.name,j.status,j.attempts,j.updated_at,
      j.started_at,j.episodes_found,j.clips_found,j.last_error
      ORDER BY CASE WHEN j.status='running' THEN 0 WHEN j.status='partial' THEN 1
      WHEN j.status='failed' THEN 2 WHEN j.status='pending' THEN 3 ELSE 4 END,
      j.updated_at DESC NULLS LAST LIMIT %s'''
    return c.execute(query,args+[limit]).fetchall()

@app.get('/',response_class=HTMLResponse)
def index(request:Request,q:str='',network:str='',status:str='',kind:str='',page:int=Query(1,ge=1,le=100000)):
    conditions=[];params=[]
    q=q.strip()[:100];network=network.strip()[:100]
    if q:
        conditions.append('(p.name ILIKE %s OR p.provider_id ILIKE %s)');params.extend([f'%{q}%',f'%{q}%'])
    if network:
        conditions.append('EXISTS(SELECT 1 FROM program_channels pc WHERE pc.program_key=p.program_key AND pc.slug=%s)');params.append(network)
    if status in STATUS_LABELS: conditions.append('j.status=%s');params.append(status)
    if kind in ('kids','program','product'): conditions.append('p.kind=%s');params.append(kind)
    where=' WHERE '+' AND '.join(conditions) if conditions else ''
    with connect() as c:
        overall=totals(c)
        count=c.execute('SELECT count(*) AS n FROM programs p JOIN jobs j USING(program_key)'+where,params).fetchone()['n']
        recs=c.execute('''SELECT p.program_key,p.name,p.kind,p.image_url,p.url,j.status,j.episodes_found,j.clips_found
                        FROM programs p JOIN jobs j USING(program_key)'''+where+
                       ' ORDER BY p.name,p.program_key LIMIT %s OFFSET %s',params+[PAGE_SIZE,(page-1)*PAGE_SIZE]).fetchall()
        channels=c.execute('SELECT slug,name FROM channels ORDER BY name').fetchall()
    return render(request,'index.html',{'programs':recs,'stats':overall,'channels':channels,
         'page':page,'pages':max(1,math.ceil(count/PAGE_SIZE)),'count':count,
         'q':q,'network':network,'status':status,'kind':kind,'section':'archive'})

@app.get('/program/{program_key}',response_class=HTMLResponse)
def program(request:Request,program_key:str,kind:str='',page:int=Query(1,ge=1,le=100000)):
    if len(program_key)>120:raise HTTPException(404)
    where='program_key=%s';params=[program_key]
    if kind in ('episode','clip'):
        where+=' AND kind=%s';params.append(kind)
    with connect() as c:
        prog=c.execute('SELECT program_key,name,kind,url,image_url,description FROM programs WHERE program_key=%s',(program_key,)).fetchone()
        if not prog:raise HTTPException(404)
        job=c.execute('SELECT status,attempts,episodes_found,clips_found,last_error,updated_at FROM jobs WHERE program_key=%s',(program_key,)).fetchone()
        count=c.execute('SELECT count(*) AS n FROM media_items WHERE '+where,params).fetchone()['n']
        records=c.execute('''SELECT item_key,title,kind,source_url,image_url,season,episode_number,broadcast_date,
                  duration_seconds,description FROM media_items WHERE '''+where+
                  ' ORDER BY season DESC NULLS LAST,episode_number DESC NULLS LAST,item_key LIMIT %s OFFSET %s',
                  params+[PAGE_SIZE,(page-1)*PAGE_SIZE]).fetchall()
        networks=c.execute('SELECT c.name FROM channels c JOIN program_channels pc ON c.slug=pc.slug WHERE pc.program_key=%s ORDER BY c.name',(program_key,)).fetchall()
    return render(request,'program.html',{'program':prog,'job':job,'items':records,'networks':networks,
      'count':count,'kind':kind,'page':page,'pages':max(1,math.ceil(count/PAGE_SIZE)),'section':'archive'})

@app.get('/channels',response_class=HTMLResponse)
def channels_page(request:Request,q:str='',group:str=''):
    q=q.strip()[:100];group=group if group in ('tv','virtual') else ''
    with connect() as c: channels=all_channels(c)
    if q:
        channels=[r for r in channels if q.casefold() in (r['name'] or '').casefold() or q.casefold() in r['slug'].casefold()]
    if group: channels=[r for r in channels if r['kind']==group]
    return render(request,'channels.html',{'channels':channels,'q':q,'group':group,'section':'channels'})

@app.get('/channels/{slug}',response_class=HTMLResponse)
def channel_page(request:Request,slug:str,status:str='',page:int=Query(1,ge=1,le=100000)):
    if not re.fullmatch(r'[a-z0-9_-]{1,70}',slug):raise HTTPException(404)
    if status not in STATUS_LABELS:status=''
    with connect() as c:
        channel=next((r for r in all_channels(c) if r['slug']==slug),None)
        if not channel:raise HTTPException(404)
        where='pc.slug=%s';values=[slug]
        if status:where+=' AND j.status=%s';values.append(status)
        count=c.execute('SELECT COUNT(*) AS n FROM program_channels pc JOIN jobs j USING(program_key) WHERE '+where,values).fetchone()['n']
        records=c.execute('''SELECT p.program_key,p.name,p.kind,p.image_url,p.url,j.status,j.episodes_found,j.clips_found
          FROM program_channels pc JOIN programs p USING(program_key) JOIN jobs j USING(program_key) WHERE '''+where+
          ' ORDER BY p.name,p.program_key LIMIT %s OFFSET %s', values+[PAGE_SIZE,(page-1)*PAGE_SIZE]).fetchall()
    return render(request,'channel.html',{'channel':channel,'programs':records,'status':status,
          'count':count,'page':page,'pages':max(1,math.ceil(count/PAGE_SIZE)),'section':'channels'})

@app.get('/dashboard',response_class=HTMLResponse)
def dashboard(request:Request,q:str='',channel:str='',status:str=''):
    q=q.strip()[:100];channel=channel.strip()[:70]
    if status not in STATUS_LABELS: status=''
    with connect() as c:
        overview=totals(c)
        recent=recent_jobs(c,q,channel,status)
        channels=c.execute('SELECT slug,name FROM channels ORDER BY name').fetchall()
        events=c.execute('SELECT id,at,level,message FROM events ORDER BY id DESC LIMIT 12').fetchall()
    return render(request,'dashboard.html',{'stats':overview,'recent':recent,'events':events,
           'channels':channels,'q':q,'channel':channel,'status':status,'section':'dashboard'})

@app.post('/api/crawler/{action}')
def crawler_action(action:str,request:Request):
    if request.headers.get('X-Requested-With')!='TWCrawlerUI':raise HTTPException(403)
    if action not in ('pause','resume'):raise HTTPException(404)
    # Reject cross-origin credentialed requests. Same-origin UI sets this header.
    origin=request.headers.get('origin')
    if origin and origin.rstrip('/') != f'{request.headers.get("x-forwarded-proto",request.url.scheme)}://{request.headers.get("host","")}'.rstrip('/'):
        raise HTTPException(403)
    value='1' if action=='resume' else '0'
    with connect() as c:
        c.execute("INSERT INTO settings(key,value) VALUES('enabled',%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",(value,))
        event(c,'info','Crawler '+action)
    return {'ok':True,'enabled':value=='1'}

@app.get('/api/stats')
def api_stats():
    with connect() as c:return totals(c)

@app.get('/api/live')
def api_live(scope:str='',q:str='',channel:str='',status:str=''):
    """Client polls every 12 seconds; all values read from committed PostgreSQL data."""
    with connect() as c:
        overview=totals(c)
        payload={'stats':overview}
        if scope in ('dashboard','channels','channel'):
            payload['channels']=[dict(r) for r in all_channels(c)]
        if scope=='dashboard':
            filtered=recent_jobs(c,q.strip()[:100],channel.strip()[:70],status,60)
            payload['jobs']=[dict(r) for r in filtered]
        return payload

@app.get('/api/channels')
def api_channels():
    with connect() as c: return {'items':all_channels(c)}

@app.get('/api/programs')
def api_programs(q:str='',limit:int=Query(30,ge=1,le=100),offset:int=Query(0,ge=0)):
    with connect() as c:
        rows=c.execute('''SELECT program_key,name,kind,url,image_url FROM programs
           WHERE name ILIKE %s ORDER BY name,program_key LIMIT %s OFFSET %s''',(f'%{q.strip()[:80]}%',limit,offset)).fetchall()
    return {'items':rows,'limit':limit,'offset':offset}

@app.get('/api/programs/{key}/media')
def api_media(key:str,limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0)):
    with connect() as c:
        rows=c.execute('''SELECT item_key,program_key,kind,provider_id,title,subtitle,source_url,
           image_url,description,season,episode_number,broadcast_date,published_at,duration_seconds
           FROM media_items WHERE program_key=%s ORDER BY kind,season,episode_number,item_key LIMIT %s OFFSET %s''',
           (key,limit,offset)).fetchall()
    return {'items':rows,'limit':limit,'offset':offset}
