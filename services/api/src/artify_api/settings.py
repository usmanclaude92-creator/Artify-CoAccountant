"""Environment-based configuration, validated at startup (ADR: no secrets in the repository)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET_MARKER = "dev-only"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARTIFY_", env_file=".env", extra="ignore")

    environment: Literal["local", "ci", "staging", "production"] = "local"

    # Four distinct DB identities (docs/database/ISOLATION_AND_RLS.md): the application never
    # connects as the schema owner and none of these roles has BYPASSRLS.
    database_url_app: str = "postgresql+psycopg://artify_app_login:dev-only-app@localhost:5432/artify"
    database_url_platform: str = "postgresql+psycopg://artify_platform_login:dev-only-platform@localhost:5432/artify"
    database_url_dispatcher: str = (
        "postgresql+psycopg://artify_dispatcher_login:dev-only-dispatcher@localhost:5432/artify"
    )
    database_url_migrate: str = "postgresql+psycopg://postgres@localhost:5432/artify"

    redis_url: str = "redis://localhost:6379/0"

    # Token signing: one secret per realm so a tenant token can never validate as a platform token.
    tenant_jwt_secret: SecretStr = SecretStr(f"{_DEV_SECRET_MARKER}-tenant-secret-change-me-0123456789")
    platform_jwt_secret: SecretStr = SecretStr(f"{_DEV_SECRET_MARKER}-platform-secret-change-me-012345")
    jwt_issuer: str = "artify"
    # Fernet key (urlsafe base64, 32 bytes) protecting TOTP secrets at rest.
    mfa_encryption_key: SecretStr = SecretStr("ZGV2LW9ubHktbWZhLWtleS0wMTIzNDU2Nzg5YWJjZGU=")

    tenant_access_ttl_seconds: int = 15 * 60
    platform_access_ttl_seconds: int = 10 * 60
    tenant_refresh_ttl_seconds: int = 30 * 24 * 3600
    platform_refresh_ttl_seconds: int = 24 * 3600
    mfa_challenge_ttl_seconds: int = 5 * 60
    email_token_ttl_seconds: int = 24 * 3600
    invitation_ttl_seconds: int = 7 * 24 * 3600

    min_password_length: int = 12
    cors_origins: list[str] = Field(default_factory=list)
    rate_limit_backend: Literal["memory", "redis"] = "memory"
    mailer_backend: Literal["console", "memory"] = "console"

    @model_validator(mode="after")
    def _no_dev_secrets_outside_dev(self) -> Settings:
        if self.environment in ("staging", "production"):
            for name in ("tenant_jwt_secret", "platform_jwt_secret", "mfa_encryption_key"):
                value = getattr(self, name).get_secret_value()
                if _DEV_SECRET_MARKER in value or value.startswith("ZGV2LW9ubHktbWZh"):
                    raise ValueError(f"{name} must be set to a real secret in {self.environment}")
            if self.rate_limit_backend != "redis":
                raise ValueError("rate_limit_backend must be 'redis' in staging/production")
            for name in ("database_url_app", "database_url_platform", "database_url_dispatcher"):
                if "dev-only" in getattr(self, name):
                    raise ValueError(f"{name} contains a development password")
        from cryptography.fernet import Fernet  # fail fast on a malformed key

        Fernet(self.mfa_encryption_key.get_secret_value().encode())
        if len(self.tenant_jwt_secret.get_secret_value()) < 32:
            raise ValueError("tenant_jwt_secret must be at least 32 characters")
        if len(self.platform_jwt_secret.get_secret_value()) < 32:
            raise ValueError("platform_jwt_secret must be at least 32 characters")
        if self.tenant_jwt_secret.get_secret_value() == self.platform_jwt_secret.get_secret_value():
            raise ValueError("tenant and platform JWT secrets must differ")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
