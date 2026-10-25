"""Helpers shared by the importer, web and scraper."""
from __future__ import annotations
import datetime
import os
from pathlib import Path


def utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


def database_url() -> str:
    value = os.getenv('DATABASE_URL', '').strip()
    if not value:
        raise RuntimeError('DATABASE_URL is missing; configure /etc/telewebion/twcrawler.env')
    return value


def private_import_dir() -> Path:
    return Path(os.getenv('TW_IMPORT_DIR','/srv/telewebion/private/imports'))
