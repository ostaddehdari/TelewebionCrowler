"""SQLite catalog, independently idempotent importer & observable crawl queue."""
from __future__ import annotations
import json, os, sqlite3, hashlib, datetime, re
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DB=Path(os.environ.get('TW_DB',str(ROOT/'data'/'telewebion.sqlite3')))
DB.parent.mkdir(parents=True,exist_ok=True)

def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
def dump(v):return json.dumps(v,ensure_ascii=False,separators=(',',':'))
def conn():
 c=sqlite3.connect(str(DB),timeout=40,check_same_thread=False)
 c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=40000');c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA foreign_keys=ON')
 return c

def init():
 with conn() as c:
  c.executescript('''
  CREATE TABLE IF NOT EXISTS channels(slug TEXT PRIMARY KEY,name TEXT NOT NULL,category TEXT,kind TEXT,icon_url TEXT,source_url TEXT,created_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS programs(program_key TEXT PRIMARY KEY,provider_id TEXT NOT NULL,kind TEXT NOT NULL,name TEXT NOT NULL,url TEXT NOT NULL,image_url TEXT,description TEXT,season_count INTEGER,raw_json TEXT,updated_at TEXT NOT NULL);
  CREATE INDEX IF NOT EXISTS idx_program_name ON programs(name);
  CREATE TABLE IF NOT EXISTS program_metadata(program_key TEXT PRIMARY KEY REFERENCES programs(program_key),source TEXT,details_json TEXT NOT NULL,updated_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS program_channels(program_key TEXT NOT NULL REFERENCES programs(program_key),slug TEXT NOT NULL REFERENCES channels(slug),PRIMARY KEY(program_key,slug));
  CREATE INDEX IF NOT EXISTS idx_pc_slug ON program_channels(slug);
  CREATE TABLE IF NOT EXISTS media_items(item_key TEXT PRIMARY KEY,program_key TEXT NOT NULL REFERENCES programs(program_key),kind TEXT NOT NULL CHECK(kind IN ('episode','clip')),provider_id TEXT,title TEXT NOT NULL,subtitle TEXT,source_url TEXT,image_url TEXT,description TEXT,season INTEGER,episode_number INTEGER,duration_seconds INTEGER,broadcast_date TEXT,published_at TEXT,view_count INTEGER,source_type TEXT,raw_json TEXT,first_seen TEXT NOT NULL,last_seen TEXT NOT NULL);
  CREATE INDEX IF NOT EXISTS idx_items_program_kind ON media_items(program_key,kind);
  CREATE INDEX IF NOT EXISTS idx_items_date ON media_items(broadcast_date);
  CREATE TABLE IF NOT EXISTS jobs(program_key TEXT PRIMARY KEY REFERENCES programs(program_key),status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,episodes_found INTEGER NOT NULL DEFAULT 0,clips_found INTEGER NOT NULL DEFAULT 0,last_error TEXT,started_at TEXT,finished_at TEXT,worker TEXT,updated_at TEXT);
  CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
  CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,at TEXT NOT NULL,level TEXT,program_key TEXT,message TEXT,details TEXT);
  CREATE INDEX IF NOT EXISTS idx_events_id ON events(id);
  CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
  INSERT OR IGNORE INTO settings(key,value) VALUES('enabled','1');
  ''')

def log(c,level,message,key=None,details=None):
 c.execute('INSERT INTO events(at,level,program_key,message,details) VALUES(?,?,?,?,?)',(utc(),level,key,str(message)[:700],dump(details)[:4000] if details is not None else None))
 # trim event log in bounded increments
 if c.execute('SELECT COUNT(*) FROM events').fetchone()[0]>150000:
  c.execute('DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY id LIMIT 10000)')

def import_v4(output_dir=None):
 """Import V4's all_programs/by_network; never delete existing child rows."""
 init();out=Path(output_dir or ROOT/'output')
 all_path=out/'all_programs.json';group_path=out/'by_network.json'
 if not all_path.exists():raise FileNotFoundError(f'No catalog at {all_path}; run `py -3 run.py build` or copy previous V4 output/.')
 programs=json.loads(all_path.read_text(encoding='utf-8-sig'))
 groups=json.loads(group_path.read_text(encoding='utf-8-sig')).get('channels',[]) if group_path.exists() else []
 # For legacy/simple import, also use networks.json as channel definitions.
 nets=json.loads((ROOT/'networks.json').read_text(encoding='utf-8')) if (ROOT/'networks.json').exists() else []
 allgroups={x['slug']:x for x in nets}
 allgroups.update({x['slug']:x for x in groups if x.get('slug')})
 now=utc();padded=0
 with conn() as c:
  for g in allgroups.values():
   slug=g['slug'];c.execute('''INSERT INTO channels(slug,name,category,kind,icon_url,source_url,created_at) VALUES(?,?,?,?,?,?,?)
    ON CONFLICT(slug) DO UPDATE SET name=excluded.name,category=excluded.category,kind=excluded.kind,icon_url=excluded.icon_url,source_url=excluded.source_url''',
    (slug,g.get('name') or slug,g.get('category'),g.get('kind','tv'),g.get('icon_url') or g.get('logo_url') or g.get('logo_fallback') or 'assets/'+slug+'.svg',g.get('source_url') or g.get('programs_url'),now))
  for i,p in enumerate(programs):
   kind=str(p.get('kind') or '').lower();id_=str(p.get('id') or '').lower()
   url=str(p.get('url') or '')
   if kind not in ('program','product','kids') or not re.fullmatch(r'0x[0-9a-f]+',id_) or '/episode/' in url:continue
   name=str(p.get('name') or p.get('title') or '').strip()
   if not name:continue
   key=f'{kind}:{id_}';c.execute('''INSERT INTO programs(program_key,provider_id,kind,name,url,image_url,description,season_count,raw_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(program_key) DO UPDATE SET name=excluded.name,url=excluded.url,image_url=COALESCE(NULLIF(excluded.image_url,''),programs.image_url), description=COALESCE(excluded.description,programs.description),season_count=COALESCE(excluded.season_count,programs.season_count),raw_json=excluded.raw_json,updated_at=excluded.updated_at''',
    (key,id_,kind,name,url,p.get('image_url'),p.get('description'),p.get('season_count'),dump(p),now))
   c.execute("INSERT OR IGNORE INTO jobs(program_key,status,updated_at) VALUES(?,'pending',?)",(key,now))
   slugs=p.get('network_slugs') or [g['slug'] for g in p.get('networks',[]) if g.get('slug')]
   for slug in set(slugs):
    if slug not in allgroups:
     c.execute('INSERT OR IGNORE INTO channels(slug,name,kind,created_at) VALUES(?,?,?,?)',(slug,slug,'virtual',now))
    c.execute('INSERT OR IGNORE INTO program_channels(program_key,slug) VALUES(?,?)',(key,slug))
   padded+=1
   if i%400==0:c.commit()
  log(c,'info',f'Imported {padded} programs, channels={len(allgroups)}',details={'source':str(out)})
 return padded

def update_item(c,item):
 """Upsert child: distinct across parent AND id, with reliable id/url or a stable derived title+number key."""
 key=item['program_key'];kind=item['kind'];pid=item.get('provider_id')
 url=item.get('source_url') or None
 if pid:unique=f'{key}|{kind}|id:{pid.lower()}'
 elif url:unique=f'{key}|{kind}|url:{url}'
 else:
  # When no ID, never merge two episodes from different seasons; skip entirely if no distinguishing evidence.
  if item.get('season') is None and item.get('episode_number') is None:return False
  unique=f'{key}|{kind}|s:{item.get("season")}|e:{item.get("episode_number")}|{item["title"]}'
 hashkey=hashlib.sha256(unique.encode()).hexdigest()[:32];now=utc()
 c.execute('''INSERT INTO media_items(item_key,program_key,kind,provider_id,title,subtitle,source_url,image_url,description,season,episode_number,duration_seconds,broadcast_date,published_at,view_count,source_type,raw_json,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
 ON CONFLICT(item_key) DO UPDATE SET title=excluded.title,subtitle=COALESCE(excluded.subtitle,media_items.subtitle),source_url=COALESCE(excluded.source_url,media_items.source_url),image_url=COALESCE(excluded.image_url,media_items.image_url),description=COALESCE(excluded.description,media_items.description),season=COALESCE(excluded.season,media_items.season),episode_number=COALESCE(excluded.episode_number,media_items.episode_number),duration_seconds=COALESCE(excluded.duration_seconds,media_items.duration_seconds),broadcast_date=COALESCE(excluded.broadcast_date,media_items.broadcast_date),published_at=COALESCE(excluded.published_at,media_items.published_at),view_count=COALESCE(excluded.view_count,media_items.view_count),source_type=excluded.source_type,raw_json=excluded.raw_json,last_seen=excluded.last_seen''',
 (hashkey,key,kind,pid,item['title'],item.get('subtitle'),url,item.get('image_url'),item.get('description'),item.get('season'),item.get('episode_number'),item.get('duration_seconds'),item.get('broadcast_date'),item.get('published_at'),item.get('view_count'),item.get('source_type'),dump(item.get('raw',{})),now,now))
 return True

def status_snapshot():
 init()
 with conn() as c:
  totals={k:c.execute(f'SELECT COUNT(*) FROM {k}').fetchone()[0] for k in ('channels','programs','media_items')}
  totals.update({row['kind']:row['n'] for row in c.execute('SELECT kind,COUNT(*) n FROM media_items GROUP BY kind')})
  totals['job_states']={row['status']:row['n'] for row in c.execute('SELECT status,COUNT(*) n FROM jobs GROUP BY status')}
  totals['latest']=dict(c.execute('SELECT id,at,level,program_key,message FROM events ORDER BY id DESC LIMIT 1').fetchone() or {})
  return totals
