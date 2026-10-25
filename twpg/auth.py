"""Signed, short-lived authentication cookies; no password or secret is stored in source."""
from __future__ import annotations
import base64
import hashlib
import hmac
import os
import secrets
import time

COOKIE = 'twcrawler_session'
MAX_AGE = 12 * 3600

def encode_password(password: str, rounds: int = 600_000) -> str:
    salt = secrets.token_bytes(20)
    key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, rounds, dklen=32)
    return f'pbkdf2_sha256${rounds}${salt.hex()}${key.hex()}'

def valid_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds_s, salt_hex, key_hex = stored.split('$')
        if scheme != 'pbkdf2_sha256' or int(rounds_s) < 100_000:
            return False
        key = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt_hex), int(rounds_s), dklen=32)
        return hmac.compare_digest(key, bytes.fromhex(key_hex))
    except (ValueError, TypeError, OverflowError):
        return False

def configured() -> bool:
    return bool(os.getenv('TWC_UI_SECRET') and os.getenv('TWC_UI_PASSWORD_HASH'))

def _key() -> bytes:
    return os.environ['TWC_UI_SECRET'].encode()

def create_session(username: str, now: int | None = None) -> str:
    now = int(time.time()) if now is None else now
    payload = f'{username}|{now}|{secrets.token_hex(16)}'
    payload64 = base64.urlsafe_b64encode(payload.encode()).decode().rstrip('=')
    signature = hmac.new(_key(), payload64.encode(), hashlib.sha256).hexdigest()
    return payload64 + '.' + signature

def read_session(value: str | None, now: int | None = None) -> str | None:
    if not value or len(value) > 512 or not configured():
        return None
    try:
        payload64, signature = value.split('.', 1)
        correct = hmac.new(_key(), payload64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(correct, signature):
            return None
        payload = base64.urlsafe_b64decode(payload64 + '=' * (-len(payload64) % 4)).decode()
        username, created, _nonce = payload.split('|', 2)
        now = int(time.time()) if now is None else now
        age = now - int(created)
        if 0 <= age <= MAX_AGE and username == os.getenv('TWC_UI_USER', 'manager'):
            return username
    except (ValueError, UnicodeError, KeyError):
        return None
    return None
