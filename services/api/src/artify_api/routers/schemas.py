"""Shared response/request models (they define the public OpenAPI contract)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    mfa: bool


class LoginResponse(BaseModel):
    status: Literal["ok", "mfa_required"]
    tokens: TokenResponse | None = None
    mfa_token: str | None = None
    mfa_enrollment_required: bool = False


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class MfaVerifyRequest(BaseModel):
    mfa_token: str
    code: str = Field(min_length=6, max_length=32)


class RefreshRequest(BaseModel):
    refresh_token: str


class MfaEnrollResponse(BaseModel):
    secret: str
    otpauth_uri: str


class MfaConfirmRequest(BaseModel):
    code: str = Field(min_length=6, max_length=12)


class MfaConfirmResponse(BaseModel):
    recovery_codes: list[str]
    access_token: str
    expires_in: int


class SessionOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    mfa_verified: bool
    user_agent: str | None
    current: bool


class MessageOut(BaseModel):
    status: str


class OrganizationOut(BaseModel):
    id: uuid.UUID
    type: Literal["accounting_firm", "independent_accountant"]
    display_name: str
    slug: str
    country: str
    default_currency: str
    locale: str
    status: Literal["active", "suspended", "closed"]


class CompanyOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    code: str
    tax_registration: str | None
    country: str
    base_currency: str
    status: Literal["active", "suspended", "archived"]
    created_at: datetime


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: str
    status: Literal["active", "suspended", "removed"]


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    company_id: uuid.UUID | None
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None


class EntitlementOut(BaseModel):
    key: str
    enforcement: str
    unit: str | None
    source: str
    unlimited: bool
    limit: int | None
    enabled: bool | None
    current: int | None


class AuditEventOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    actor_type: str
    actor_id: uuid.UUID | None
    action: str
    entity_type: str | None
    entity_id: str | None
    company_id: uuid.UUID | None
    summary: dict[str, object]
