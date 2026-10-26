"""Offline regression fixtures synthesized from the user-supplied JSON structure.
No real session tokens and no PostgreSQL are needed.
"""
import importlib
import json
import sys
import types
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))

# Only for tests: a minimal stub makes it possible to import crawler without
# the psycopg system dependency, which is installed on the actual Ubuntu host.
if 'psycopg' not in sys.modules:
    try: import psycopg
    except ImportError:
        fake=types.ModuleType('psycopg')
        fake.rows=types.ModuleType('psycopg.rows')
        fake.rows.dict_row=object()
        sys.modules['psycopg']=fake
        sys.modules['psycopg.rows']=fake.rows

from newtwcrawler.ingest import unique_kind, source_kind, doc_hash, index_document
from newtwcrawler.crawler import pick_rows, params_key

class FakeDB:
    def __init__(self):self.sql=[]
    def execute(self,query,params=None):
        self.sql.append((query,params))
        return []


def test_program_episode_vs_clip():
    assert source_kind({'EpisodeID':'0x1','is_clip':True},'$.body.queryProgram[0].episodes[0]') is True
    assert source_kind({'EpisodeID':'0x1'},'$.body.getProgram.episodes[0]') is False
    assert unique_kind({'ProgramID':'0xABC'}) == ('program','0xabc')


def test_channel_pagination():
    d={'body':{'queryChannel':[{'ChannelID':'0x1','programs':[{'ProgramID':'0x2'}]}]},'message':'success'}
    assert len(pick_rows(d,'channel_programs'))==1
    assert len(pick_rows(d,'program_page') or [])==0


def test_tag_items():
    j={'body':{'getEpisodesByTagId':{'episodes':[{'EpisodeID':'0x12','is_clip':True}]}}}
    assert len(pick_rows(j,'tag_episodes'))==1


def test_preserve_unknown_fields_and_links():
    db=FakeDB()
    payload={'body':{'getProgram':{'ProgramID':'0xabc','episodes':[{'EpisodeID':'0x123','title':'قسمت تست','is_clip':False,'custom_future_field':{'x':'y'}}]}}}
    counts,jobs=index_document(db,payload,task_type='program_page')
    assert counts['program']==1
    assert counts['episode']==1
    assert '0xabc' in jobs
    records=[params for q,params in db.sql if 'INSERT INTO entities' in q]
    assert any('custom_future_field' in str(p) for p in records)
    assert any('INSERT INTO links' in q for q,p in db.sql)
    assert len(doc_hash(payload))==64


def test_clip_schema_unknown_is_not_silently_complete():
    try:pick_rows({'body':{'unknown_items':[]}},'program_clips')
    except ValueError:pass
    else:raise AssertionError('Unknown shape must be reviewed')


def test_public_archive_routes_and_secret_masking():
    assert params_key('page',{'Name':'kids'})==params_key('page',{'Name':'kids'})
    from newtwcrawler.web import app, mask
    routes = {getattr(r, 'path', ''):r for r in app.routes}
    assert '/login' not in routes and '/logout' not in routes
    assert '/' in routes and '/dashboard' in routes and '/api/stats' in routes
    assert all('GET' in (getattr(routes[p], 'methods', set()) or set()) for p in ('/', '/dashboard', '/api/stats'))
    private = {'content_token':'eyJabc.xyz.secret','children':[{'access_token':'TEST','safe_title':'سلام'}]}
    sanitized = mask(private)
    assert 'محافظت' in sanitized['content_token']
    assert 'محافظت' in sanitized['children'][0]['access_token']
    assert sanitized['children'][0]['safe_title']=='سلام'


def test_public_http_pages_without_session(monkeypatch):
    from contextlib import contextmanager
    from fastapi.testclient import TestClient
    from newtwcrawler import web

    class Result:
        def __init__(self, query):
            self.query = query.lower()
        def fetchone(self):
            return {'n':0} if 'count(*) as n' in self.query else None
        def fetchall(self):
            return []
        def __iter__(self):
            return iter([])
    class DB:
        def execute(self, query, args=None):
            return Result(query)
    @contextmanager
    def dummy_connect():
        yield DB()
    monkeypatch.setattr(web, 'connect', dummy_connect)
    monkeypatch.setattr(web, 'statistics', lambda: {'entities':{}, 'episodes':0, 'clips':0,
                   'undetermined':0, 'queue':{}, 'responses':0, 'last':None})
    with TestClient(web.app) as client:
        assert client.get('/').status_code == 200
        assert client.get('/dashboard').status_code == 200
        assert client.get('/api/stats').status_code == 200
        assert client.get('/login').status_code == 404
        assert client.post('/logout').status_code == 404
        assert 'دسترسی آزاد' in client.get('/').text
