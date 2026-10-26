import os
from pathlib import Path

BASE_PATH = '/newtwcrawler'
DATA_PATH = Path(__file__).resolve().parent / 'data'

def database_url():
    v = os.environ.get('NEWTW_DATABASE_URL', '').strip()
    if not v:
        raise RuntimeError('NEWTW_DATABASE_URL is missing')
    return v
