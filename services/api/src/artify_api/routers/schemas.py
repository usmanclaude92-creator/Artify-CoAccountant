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
    # profile (all optional: companies created before Phase 3 have none of these)
    legal_name: str | None = None
    registration_number: str | None = None
    company_type: str | None = None
    industry: str | None = None
    city: str | None = None
    address_line: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    # accounting settings owned by this company alone
    financial_year_start_month: int = 1
    financial_year_start_day: int = 1
    accounting_basis: Literal["accrual", "cash"] | None = None
    vat_registered: bool = False
    vat_number: str | None = None
    tax_jurisdiction: str | None = None
    updated_at: datetime | None = None


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
    # Phase 3 additions (all optional so older clients keep working)
    state: Literal["pending", "accepted", "revoked", "declined", "expired"] = "pending"
    company_ids: list[uuid.UUID] = []
    resent_count: int = 0
    created_at: datetime | None = None
    invited_by_name: str | None = None
    declined_at: datetime | None = None
    email_delivery: dict[str, object] | None = None


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


class OrganizationUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    locale: Literal["en", "ar"] | None = None


class RoleOut(BaseModel):
    key: str
    scope: Literal["organization", "company"]
    name: str
    permissions: list[str]
    # organization roles only (None for company roles)
    rank: int | None = None
    invitable: bool = False
    assignable_to_companies: bool = False
    reaches_all_companies: bool = False
    mfa_required: bool = False
    counts_as_accountant: bool = False


class PermissionOut(BaseModel):
    key: str
    scope: str
    description: str
