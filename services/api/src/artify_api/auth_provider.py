"""Authentication seam (decision D-1: in-house today, replaceable later).

Routes depend only on this interface. `LocalAuthProvider` is the in-house implementation
(Argon2id, rotating refresh tokens, TOTP) living in `auth_service`. Adopting an external identity
provider later means writing another implementation of this protocol, not changing the routes.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy import Connection

from artify_api import audit, auth_service
from artify_api.auth_types import LoginResult, TokenPair
from artify_api.security.tokens import Realm


class AuthProvider(Protocol):
    def signup(
        self,
        *,
        email: str,
        password: str,
        display_name: str,
        organization_name: str,
        organization_type: str,
        country: str,
        default_currency: str,
        locale: str,
        meta: audit.RequestMeta,
    ) -> None: ...

    def verify_email(self, token: str) -> None: ...

    def login(self, realm: Realm, email: str, password: str, meta: audit.RequestMeta) -> LoginResult: ...

    def verify_mfa(self, realm: Realm, mfa_token: str, code: str, meta: audit.RequestMeta) -> TokenPair: ...

    def refresh(self, realm: Realm, refresh_token: str) -> TokenPair: ...

    def revoke_session(self, conn: Connection, session_id: uuid.UUID, reason: str) -> bool: ...

    def request_password_reset(self, email: str, meta: audit.RequestMeta) -> None: ...

    def confirm_password_reset(self, token: str, new_password: str, meta: audit.RequestMeta) -> None: ...

    def mfa_enroll(self, conn: Connection, realm: Realm, subject: uuid.UUID, account_label: str) -> tuple[str, str]: ...

    def mfa_confirm(
        self, conn: Connection, realm: Realm, subject: uuid.UUID, session_id: uuid.UUID, code: str
    ) -> tuple[list[str], TokenPair]: ...


class LocalAuthProvider:
    """In-house implementation; a thin adapter over `auth_service`."""

    def signup(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        auth_service.signup(**kwargs)

    def verify_email(self, token: str) -> None:
        auth_service.verify_email(token)

    def login(self, realm: Realm, email: str, password: str, meta: audit.RequestMeta) -> LoginResult:
        return auth_service.login(realm, email, password, meta)

    def verify_mfa(self, realm: Realm, mfa_token: str, code: str, meta: audit.RequestMeta) -> TokenPair:
        return auth_service.verify_mfa(realm, mfa_token, code, meta)

    def refresh(self, realm: Realm, refresh_token: str) -> TokenPair:
        return auth_service.refresh(realm, refresh_token)

    def revoke_session(self, conn: Connection, session_id: uuid.UUID, reason: str) -> bool:
        return auth_service.revoke_session(conn, session_id, reason)

    def request_password_reset(self, email: str, meta: audit.RequestMeta) -> None:
        auth_service.request_password_reset(email, meta)

    def confirm_password_reset(self, token: str, new_password: str, meta: audit.RequestMeta) -> None:
        auth_service.confirm_password_reset(token, new_password, meta)

    def mfa_enroll(self, conn: Connection, realm: Realm, subject: uuid.UUID, account_label: str) -> tuple[str, str]:
        return auth_service.mfa_enroll(conn, realm, subject, account_label)

    def mfa_confirm(
        self, conn: Connection, realm: Realm, subject: uuid.UUID, session_id: uuid.UUID, code: str
    ) -> tuple[list[str], TokenPair]:
        return auth_service.mfa_confirm(conn, realm, subject, session_id, code)


_provider: AuthProvider = LocalAuthProvider()  # static check: LocalAuthProvider satisfies the protocol


def get_auth_provider() -> AuthProvider:
    return _provider


def set_auth_provider(provider: AuthProvider | None) -> None:
    global _provider
    _provider = provider or LocalAuthProvider()
