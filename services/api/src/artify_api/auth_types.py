"""Provider-neutral authentication types shared by routes and AuthProvider implementations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    mfa: bool
    session_id: uuid.UUID


@dataclass(frozen=True)
class LoginResult:
    mfa_required: bool
    mfa_token: str | None = None
    tokens: TokenPair | None = None
    mfa_enrollment_required: bool = False
