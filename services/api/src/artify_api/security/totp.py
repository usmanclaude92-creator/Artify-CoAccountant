"""TOTP multi-factor authentication with encrypted-at-rest secrets and replay protection."""

from __future__ import annotations

import hashlib
import secrets
import time

import pyotp
from cryptography.fernet import Fernet, InvalidToken

from artify_api.settings import get_settings

_STEP = 30
ISSUER = "Artify Co-Accountant"


def _fernet() -> Fernet:
    return Fernet(get_settings().mfa_encryption_key.get_secret_value().encode())


def new_secret() -> str:
    return pyotp.random_base32()


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:  # key rotated without re-encryption, or corrupted data
        raise RuntimeError("MFA secret cannot be decrypted") from exc


def provisioning_uri(secret: str, account: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=ISSUER)


def verify_code(secret: str, code: str, last_used_step: int | None, *, now: float | None = None) -> int | None:
    """Return the accepted time step, or None. Steps <= last_used_step are rejected (replay)."""
    code = code.strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return None
    ts = time.time() if now is None else now
    current = int(ts // _STEP)
    totp = pyotp.TOTP(secret)
    for step in (current - 1, current, current + 1):  # +-30s clock skew tolerance
        if last_used_step is not None and step <= last_used_step:
            continue
        if secrets.compare_digest(totp.at(step * _STEP), code):
            return step
    return None


def new_recovery_codes(n: int = 10) -> list[str]:
    return [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(n)]


def hash_recovery_code(code: str) -> bytes:
    return hashlib.sha256(code.strip().lower().encode()).digest()
