import hashlib
import hmac
import json
import os
import secrets
import time
from fastapi import HTTPException, Request


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 210000).hex()
    return f"{salt}:{value}"


def check_password(password, stored):
    salt = stored.split(":")[0]
    return hmac.compare_digest(password_hash(password, salt), stored)


def internal_headers(body):
    timestamp = str(int(time.time()))
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    key = os.environ["SHOPAGENT_INTERNAL_KEY"]
    signature = hmac.new(key.encode(), (timestamp + payload).encode(), hashlib.sha256).hexdigest()
    return {"X-Service-Time": timestamp, "X-Service-Signature": signature}


async def verify_internal(request: Request):
    timestamp = request.headers.get("X-Service-Time", "")
    signature = request.headers.get("X-Service-Signature", "")
    try:
        if abs(time.time() - int(timestamp)) > 60:
            raise ValueError()
        body = await request.json()
        payload = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        expected = hmac.new(os.environ["SHOPAGENT_INTERNAL_KEY"].encode(), (timestamp + payload).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
    except (ValueError, KeyError):
        raise HTTPException(401, "内部服务签名无效")
    return body
