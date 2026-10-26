import base64
import hashlib
import hmac
import os
import secrets
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

COOKIE='newtw_admin'

def hash_password(password):
    salt=secrets.token_bytes(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,450000)
    return 'pbkdf2$'+base64.urlsafe_b64encode(salt).decode()+'$'+base64.urlsafe_b64encode(digest).decode()

def verify(password):
    saved=os.environ.get('NEWTW_PASSWORD_HASH','')
    try:
        algo, salt, digest=saved.split('$')
        if algo!='pbkdf2':return False
        real=hashlib.pbkdf2_hmac('sha256',password.encode(),base64.urlsafe_b64decode(salt),450000)
        return hmac.compare_digest(real,base64.urlsafe_b64decode(digest))
    except (ValueError,TypeError):return False

def signer():
    key=os.environ.get('NEWTW_SESSION_SECRET','')
    if len(key)<48:raise RuntimeError('Missing session secret')
    return URLSafeTimedSerializer(key,salt='newtw-ui-v1')

def new_session():
    return signer().dumps({'user':'manager','nonce':secrets.token_hex(8)})

def is_logged(token):
    if not token:return False
    try:
        data=signer().loads(token,max_age=8*3600)
        return data.get('user')=='manager'
    except (BadSignature,SignatureExpired,ValueError):return False
