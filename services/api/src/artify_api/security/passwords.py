"""Password hashing (Argon2id) and policy."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from artify_api.errors import Unprocessable
from artify_api.settings import get_settings

_hasher = PasswordHasher()  # argon2-cffi defaults to Argon2id with RFC 9106 low-memory parameters

# A deliberately small denylist. Production should add a breached-password check (k-anonymity
# range lookup or an offline list); that integration is tracked for Phase 12 hardening.
_COMMON = frozenset(
    {
        "password",
        "password1",
        "123456789012",
        "qwertyuiop12",
        "letmein12345",
        "administrator",
        "welcome12345",
        "iloveyou1234",
        "changeme1234",
    }
)

# Hash of a throwaway password: verified when the account does not exist so that response time
# does not reveal whether an email is registered.
_DUMMY_HASH = _hasher.hash("artify-dummy-password-for-timing-equalization")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    """Constant-work verification: always performs one Argon2 verify."""
    try:
        _hasher.verify(stored_hash or _DUMMY_HASH, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return stored_hash is not None


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)


def validate_password_policy(password: str, *, email: str | None = None) -> None:
    minimum = get_settings().min_password_length
    if len(password) < minimum:
        raise Unprocessable(f"Password must be at least {minimum} characters.")
    if len(password) > 256:
        raise Unprocessable("Password is too long.")
    lowered = password.lower()
    if lowered in _COMMON:
        raise Unprocessable("This password is too common.")
    if email and lowered == email.lower():
        raise Unprocessable("Password must not equal the email address.")
