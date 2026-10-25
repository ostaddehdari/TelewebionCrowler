"""No network needed. Validate the original SQLite schema and selected normalization rules."""
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

from twpg.import_sqlite import TABLES,checked_sqlite,clean,validate_tables
from twpg.worker import item_identity
from twpg.scraper import normalize,parts_from_json

def test_item_identity_is_deterministic_and_matches_old_algorithm():
    item={'program_key':'kids:0xc208c07','kind':'episode','provider_id':'0xc2250df','title':'عنوان'}
    actual=item_identity(item)
    assert actual == hashlib.sha256(b'kids:0xc208c07|episode|id:0xc2250df').hexdigest()[:32]
    assert item_identity(item)==actual
    assert item_identity(dict(item,kind='clip'))!=actual

def test_incomplete_item_is_not_invented():
    assert item_identity({'program_key':'a','kind':'episode','title':'Unknown'}) is None
    p={'program_key':'program:0xabc','image_url':None}
    assert normalize({'content_type':'SERIAL','content_id':'0xabc','title':'Just a parent'},p) is None

def test_episode_and_clip_only():
    p={'program_key':'kids:0xabc','image_url':None}
    got=parts_from_json({'data':[{'content_type':'SERIALPARTS','content_id':'0xabc1','title':'قسمت اول','episode_number':1},
                                 {'content_type':'CLIP','content_id':'0xabc2','title':'تیزر'},
                                 {'content_type':'SERIAL','content_id':'0xabc','title':'سریال'}]},p)
    assert len(got)==2
    assert {x['kind'] for x in got}=={'episode','clip'}

def test_sqlite_validation():
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp)/'demo.sqlite3'
        with sqlite3.connect(p) as c:
            for table in TABLES:
                c.execute(f'CREATE TABLE "{table}"(example TEXT)')
        c=checked_sqlite(p)
        validate_tables(c)
        c.close()

def test_string_nul_stripping():
    assert clean(('a\x00b',34,None)) == ('ab',34,None)
