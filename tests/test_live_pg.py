"""Opt-in DB smoke. Use a DEDICATED temporary PostgreSQL database!"""
import os
import sqlite3
from pathlib import Path
import pytest

pytestmark = pytest.mark.skipif(not os.getenv('TW_TEST_DATABASE_URL'),reason='TW_TEST_DATABASE_URL not provided')

def test_schema_and_sqlite_import_idempotence(tmp_path,monkeypatch):
    from twpg.db import initialize,connect
    from twpg.import_sqlite import run
    monkeypatch.setenv('DATABASE_URL',os.environ['TW_TEST_DATABASE_URL'])
    initialize()
    file=tmp_path/'original.sqlite3'
    schema={
      'channels':'slug TEXT PRIMARY KEY,name TEXT NOT NULL,category TEXT,kind TEXT,icon_url TEXT,source_url TEXT,created_at TEXT NOT NULL',
      'programs':'program_key TEXT PRIMARY KEY,provider_id TEXT NOT NULL,kind TEXT NOT NULL,name TEXT NOT NULL,url TEXT NOT NULL,image_url TEXT,description TEXT,season_count INTEGER,raw_json TEXT,updated_at TEXT NOT NULL',
      'program_channels':'program_key TEXT NOT NULL,slug TEXT NOT NULL,PRIMARY KEY(program_key,slug)',
      'program_metadata':'program_key TEXT PRIMARY KEY,source TEXT,details_json TEXT NOT NULL,updated_at TEXT NOT NULL',
      'media_items':'item_key TEXT PRIMARY KEY,program_key TEXT NOT NULL,kind TEXT NOT NULL,provider_id TEXT,title TEXT NOT NULL,subtitle TEXT,source_url TEXT,image_url TEXT,description TEXT,season INTEGER,episode_number INTEGER,duration_seconds INTEGER,broadcast_date TEXT,published_at TEXT,view_count INTEGER,source_type TEXT,raw_json TEXT,first_seen TEXT NOT NULL,last_seen TEXT NOT NULL',
      'jobs':'program_key TEXT PRIMARY KEY,status TEXT NOT NULL,attempts INTEGER,episodes_found INTEGER,clips_found INTEGER,last_error TEXT,started_at TEXT,finished_at TEXT,worker TEXT,updated_at TEXT',
      'events':'id INTEGER PRIMARY KEY,at TEXT NOT NULL,level TEXT,program_key TEXT,message TEXT,details TEXT',
      'settings':'key TEXT PRIMARY KEY,value TEXT NOT NULL'}
    with sqlite3.connect(file) as c:
        for table,cols in schema.items(): c.execute(f'CREATE TABLE {table}({cols})')
        c.execute("INSERT INTO channels(slug,name,created_at) VALUES('tv1','شبکه اول','2026-10-23')")
        c.execute("INSERT INTO programs(program_key,provider_id,kind,name,url,updated_at) VALUES('program:0xab','0xab','program','برنامه آزمایشی','https://telewebion.net/program/0xab','2026-10-23')")
        c.execute("INSERT INTO program_channels VALUES('program:0xab','tv1')")
        c.execute("INSERT INTO media_items(item_key,program_key,kind,title,first_seen,last_seen) VALUES('abcd','program:0xab','episode','قسمت اول','2026-10-23','2026-10-23')")
        c.execute("INSERT INTO jobs(program_key,status,attempts,episodes_found,clips_found) VALUES('program:0xab','done',1,1,0)")
        c.execute("INSERT INTO events(id,at,level,message) VALUES(100,'2026-10-23','info','test')")
        c.execute("INSERT INTO settings(key,value) VALUES('enabled','0')")
    first=run(file,batch_size=2)
    second=run(file,batch_size=2)
    assert first['media_items']==1 and second=={}
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM media_items WHERE item_key='abcd'").fetchone()['n']==1
        assert db.execute("SELECT status FROM jobs WHERE program_key='program:0xab'").fetchone()['status']=='done'
        assert db.execute('SELECT nextval(pg_get_serial_sequence(\'events\',\'id\')) AS id').fetchone()['id']>100
