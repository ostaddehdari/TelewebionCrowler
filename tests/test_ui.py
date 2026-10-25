"""Offline contract checks; fake pg module permits running even without server dependencies."""
import os
import pathlib
import sys
import types
import time
import unittest
from unittest.mock import patch

# Fake psycopg only for offline import of service code; production uses real PostgreSQL.
pg = types.ModuleType('psycopg')
pg.connect = lambda *a,**kw: None
pg.Connection = object
rows = types.ModuleType('psycopg.rows')
rows.dict_row = object()
sys.modules.setdefault('psycopg',pg)
sys.modules.setdefault('psycopg.rows',rows)
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from twpg.auth import create_session, read_session, encode_password, valid_password
from twpg import web
from fastapi.testclient import TestClient

class FakeDb:
    def execute(self, sql, params=None):
        if 'SELECT 1' in sql:return FakeResult([{'?column?':1}])
        if 'SELECT count(*) AS n FROM channels' in sql:return FakeResult([{'n':82}])
        if 'SELECT count(*) AS n FROM programs' in sql:return FakeResult([{'n':49822}])
        if 'SELECT count(*) AS n FROM media_items' in sql:return FakeResult([{'n':184539}])
        if 'SELECT count(*) AS n FROM jobs' in sql:return FakeResult([{'n':49822}])
        if 'SELECT kind,count(*)' in sql:return FakeResult([{'kind':'episode','n':126905},{'kind':'clip','n':57634}])
        if 'SELECT status,count(*)' in sql:return FakeResult([{'status':'pending','n':11472},{'status':'partial','n':32907},{'status':'done','n':5231}])
        if "SELECT value FROM settings" in sql:return FakeResult([{'value':'0'}])
        if 'COALESCE(string_agg' in sql:return FakeResult([{'program_key':'abcd','name':'نام آزمایشی','status':'partial','attempts':2,'updated_at':'2026-10-25','started_at':'2026-10-24','episodes_found':3,'clips_found':2,'last_error':None,'networks':'شبکه آزمایشی','slugs':'test'}])
        if 'SELECT slug,name FROM channels' in sql:return FakeResult([{'slug':'test','name':'شبکه آزمایشی'}])
        if 'SELECT id,at,level,message' in sql:return FakeResult([])
        if 'WITH jobs_by_channel' in sql:return FakeResult([dict(slug='test',name='شبکه آزمایشی',kind='tv',category='tv',icon_url=None,source_url=None,programs=10,pending=5,partial=2,done=3,failed=0,running=0,empty_review=0,episodes=12,clips=4)])
        if 'COUNT(*) AS n FROM program_channels' in sql:return FakeResult([{'n':0}])
        if 'SELECT p.program_key' in sql:return FakeResult([])
        if 'SELECT p.program_key' in sql or 'FROM programs p JOIN jobs j' in sql:return FakeResult([])
        if 'JOIN jobs j USING' in sql:return FakeResult([])
        return FakeResult([])
    def __enter__(self):return self
    def __exit__(self,*args):return False
class FakeResult:
    def __init__(self,rows):self.rows=rows
    def fetchone(self):return self.rows[0] if self.rows else None
    def fetchall(self):return self.rows
    def __iter__(self):return iter(self.rows)

class UITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['TWC_UI_USER']='manager'
        os.environ['TWC_UI_SECRET']='test-secret-only'
        os.environ['TWC_UI_PASSWORD_HASH']=encode_password('a-long-good-password')
        cls.client=TestClient(web.app,follow_redirects=False)
    def test_password(self):
        self.assertTrue(valid_password('a-long-good-password',os.environ['TWC_UI_PASSWORD_HASH']))
        self.assertFalse(valid_password('bad',os.environ['TWC_UI_PASSWORD_HASH']))
    def test_expiring_session(self):
        t=int(time.time());v=create_session('manager',now=t)
        self.assertEqual(read_session(v,now=t+5),'manager')
        self.assertIsNone(read_session(v,now=t+13*3600))
        self.assertIsNone(read_session(v+'x',now=t+5))
    def test_login_and_auth_guard(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/dashboard').status_code,303)
        self.assertEqual(self.client.get('/api/live').status_code,401)
        self.assertEqual(self.client.get('/login').status_code,200)
        self.assertEqual(self.client.post('/login',data={'username':'manager','password':'bad'}).status_code,401)
        res=self.client.post('/login',data={'username':'manager','password':'a-long-good-password'})
        self.assertEqual(res.status_code,303)
        self.assertIn('Secure',res.headers['set-cookie'])
        self.assertIn('HttpOnly',res.headers['set-cookie'])
        self.assertIn('Path=/twcrawler/',res.headers['set-cookie'])
    def test_dashboard_and_channel(self):
        with patch.object(web,'connect',return_value=FakeDb()):
            session=create_session('manager')
            self.client.cookies.set('twcrawler_session',session)
            for path in ['/dashboard','/channels','/channels/test','/api/live?scope=dashboard']:
                res=self.client.get(path)
                self.assertEqual(res.status_code,200,(path,res.text[:300]))
            live=self.client.get('/api/live?scope=dashboard').json()
            self.assertEqual(live['stats']['episodes'],126905)
            self.assertEqual(live['stats']['clips'],57634)
            self.assertEqual(live['channels'][0]['slug'],'test')
            self.assertEqual(live['jobs'][0]['networks'],'شبکه آزمایشی')
            self.assertIn('شبکه آزمایشی',self.client.get('/dashboard').text)
            self.assertIn('قسمت‌های استخراج‌شده',self.client.get('/channels/test').text)

if __name__=='__main__':unittest.main()
