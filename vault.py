import hashlib
import hmac
import os
import time
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MAILBOX_KEY = DATA / "mailbox.key"
SESSION_KEY = DATA / "session.key"
PREFIX = "enc1:"


def _fernet() -> Fernet:
    DATA.mkdir(exist_ok=True)
    if not MAILBOX_KEY.exists():
        MAILBOX_KEY.write_bytes(Fernet.generate_key())
        os.chmod(MAILBOX_KEY, 0o600)
    return Fernet(MAILBOX_KEY.read_bytes())


def seal(value: str) -> str:
    text = "" if value is None else str(value)
    if not text or text.startswith(PREFIX):
        return text
    return PREFIX + _fernet().encrypt(text.encode()).decode()


def open_secret(value: str) -> str:
    text = "" if value is None else str(value)
    if not text.startswith(PREFIX):
        return text
    try:
        return _fernet().decrypt(text[len(PREFIX):].encode()).decode()
    except (InvalidToken, ValueError):
        return text


def session_key() -> bytes:
    DATA.mkdir(exist_ok=True)
    if not SESSION_KEY.exists():
        SESSION_KEY.write_bytes(os.urandom(32))
        os.chmod(SESSION_KEY, 0o600)
    return SESSION_KEY.read_bytes()


def rotate_session_key() -> None:
    DATA.mkdir(exist_ok=True)
    SESSION_KEY.write_bytes(os.urandom(32))
    os.chmod(SESSION_KEY, 0o600)


def make_session(user_id: int, days: int = 14) -> str:
    exp = int(time.time()) + days * 86400
    body = f"{int(user_id)}.{exp}".encode()
    sig = hmac.new(session_key(), body, hashlib.sha256).hexdigest()
    return f"{int(user_id)}.{exp}.{sig}"


def session_user(token: str) -> Optional[int]:
    parts = (token or "").split(".")
    if len(parts) != 3:
        return None
    user_text, exp_text, sig = parts
    if not user_text.isdigit() or not exp_text.isdigit():
        return None
    body = f"{user_text}.{exp_text}".encode()
    expected = hmac.new(session_key(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    if int(exp_text) <= time.time():
        return None
    return int(user_text)


def session_ok(token: str) -> bool:
    return session_user(token) is not None


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return salt.hex() + ":" + hashed.hex()


def password_ok(password: str, stored: str) -> bool:
    if not stored or ":" not in stored:
        return False
    salt_hex, hash_hex = stored.split(":", 1)
    try:
        hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 200_000)
    except ValueError:
        return False
    return hmac.compare_digest(hashed.hex(), hash_hex)
