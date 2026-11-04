"""Access tokens (JWT, one signing secret and audience per realm) and opaque refresh tokens."""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Literal

import jwt

from artify_api.errors import Unauthenticated
from artify_api.settings import get_settings

Realm = Literal["tenant", "platform"]
_AUD = {"tenant": "artify-tenant", "platform": "artify-platform"}
_ALG = "HS256"


@dataclass(frozen=True)
class AccessClaims:
    subject: uuid.UUID  # user id (tenant realm) or platform admin id (platform realm)
    session_id: uuid.UUID
    realm: Realm
    mfa: bool
    typ: str  # 'access' | 'mfa_challenge'
    expires_at: int


def _secret(realm: Realm) -> str:
    s = get_settings()
    return (s.tenant_jwt_secret if realm == "tenant" else s.platform_jwt_secret).get_secret_value()


def _ttl(realm: Realm, typ: str) -> int:
    s = get_settings()
    if typ == "mfa_challenge":
        return s.mfa_challenge_ttl_seconds
    return s.tenant_access_ttl_seconds if realm == "tenant" else s.platform_access_ttl_seconds


def issue_token(
    realm: Realm, subject: uuid.UUID, session_id: uuid.UUID, *, mfa: bool, typ: str = "access"
) -> tuple[str, int]:
    now = int(time.time())
    exp = now + _ttl(realm, typ)
    payload = {
        "iss": get_settings().jwt_issuer,
        "aud": _AUD[realm],
        "sub": str(subject),
        "sid": str(session_id),
        "iat": now,
        "exp": exp,
        "typ": typ,
        "mfa": mfa,
    }
    return jwt.encode(payload, _secret(realm), algorithm=_ALG), exp - now


def decode_token(realm: Realm, token: str, *, expected_typ: str = "access") -> AccessClaims:
    try:
        data = jwt.decode(
            token,
            _secret(realm),
            algorithms=[_ALG],
            audience=_AUD[realm],
            issuer=get_settings().jwt_issuer,
            options={"require": ["exp", "iat", "sub", "sid", "aud", "iss"]},
        )
        if data.get("typ") != expected_typ:
            raise Unauthenticated("Invalid token type.")
        return AccessClaims(
            subject=uuid.UUID(data["sub"]),
            session_id=uuid.UUID(data["sid"]),
            realm=realm,
            mfa=bool(data.get("mfa", False)),
            typ=data["typ"],
            expires_at=int(data["exp"]),
        )
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise Unauthenticated("Invalid or expired token.") from exc


def new_opaque_token() -> tuple[str, bytes]:
    """Return (plaintext, sha256 digest). Only the digest is ever stored."""
    plaintext = secrets.token_urlsafe(32)
    return plaintext, hash_token(plaintext)


def hash_token(plaintext: str) -> bytes:
    return hashlib.sha256(plaintext.encode("utf-8")).digest()
