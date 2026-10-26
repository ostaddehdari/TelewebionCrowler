import contextlib
import psycopg
from psycopg.rows import dict_row
from pathlib import Path
from .config import database_url

@contextlib.contextmanager
def connect():
    with psycopg.connect(database_url(), row_factory=dict_row, connect_timeout=10) as db:
        yield db

def init():
    with connect() as db:
        db.execute((Path(__file__).parent / 'schema.sql').read_text())
