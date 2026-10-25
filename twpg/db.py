from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
import psycopg
from psycopg.rows import dict_row
from .utils import database_url, utc

SCHEMA = Path(__file__).with_name('schema.sql')

@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    with psycopg.connect(database_url(), row_factory=dict_row, connect_timeout=12) as c:
        yield c

def initialize() -> None:
    with connect() as c:
        c.execute(SCHEMA.read_text(encoding='utf-8'))

def event(c, level: str, message: str, program_key: str | None = None, details: str | None = None) -> None:
    c.execute('INSERT INTO events(at,level,program_key,message,details) VALUES(%s,%s,%s,%s,%s)',
              (utc(),level,program_key,message[:700],details[:4000] if details else None))

def stats(c) -> dict:
    totals = {}
    for table in ('channels','programs','media_items','jobs'):
        totals[table] = c.execute(f'SELECT count(*) AS n FROM {table}').fetchone()['n']
    totals['media_kinds'] = {r['kind']:r['n'] for r in c.execute('SELECT kind,count(*) AS n FROM media_items GROUP BY kind')}
    totals['job_states'] = {r['status']:r['n'] for r in c.execute('SELECT status,count(*) AS n FROM jobs GROUP BY status')}
    totals['enabled'] = c.execute("SELECT value FROM settings WHERE key='enabled'").fetchone()['value']=='1'
    return totals
