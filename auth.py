"""パスワードの保存と照合 (平文では保存せず、ソルト付きハッシュだけを settings.json に置く)"""
import hashlib
import hmac
import secrets

ITERATIONS = 200_000


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), ITERATIONS).hex()


def make_record(password):
    salt = secrets.token_hex(16)
    return {"password_salt": salt, "password_hash": _hash(password, salt)}


def verify(settings, password):
    salt, expected = settings.get("password_salt"), settings.get("password_hash")
    if not salt or not expected:
        return False
    return hmac.compare_digest(_hash(password or "", salt), expected)


def is_set(settings):
    return bool(settings.get("password_hash"))
