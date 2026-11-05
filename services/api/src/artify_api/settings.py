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
    # Connection pool per database identity. Serverless hosting (one tiny instance per request burst, small pooler) sets 1 / 0.
    db_pool_size: int = 10
    db_max_overflow: int = 10

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
    mailer_backend: Literal["console", "memory", "smtp"] = "console"
    log_format: Literal["auto", "text", "json"] = "auto"  # auto: text in local, json elsewhere
    log_level: str = "INFO"

    # E-mail (used when mailer_backend = "smtp"). Provider choice is decision D-3 (hosting).
    web_base_url: str = "http://localhost:3000"  # origin used in e-mailed links
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = True
    mail_from: str = "Artify Co-Accountant <no-reply@localhost>"

    # Document storage (docs/documents/CLOUD_STORAGE_ARCHITECTURE.md). Objects are private; there are no public URLs.
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_root: str = ".data/storage"  # local backend only (development and tests)
    storage_s3_bucket: str | None = None
    storage_s3_region: str | None = None
    storage_s3_endpoint_url: str | None = None  # S3-compatible endpoints (MinIO, R2, ...)
    storage_s3_access_key_id: SecretStr | None = None  # falls back to the SDK credential chain when unset
    storage_s3_secret_access_key: SecretStr | None = None
    storage_s3_sse: str | None = None  # e.g. "AES256" or "aws:kms"
    storage_tmp_dir: str | None = (
        None  # upload staging (defaults to the system temp dir); shared volume when several replicas run
    )
    upload_chunk_max_bytes: int = 16 * 1024 * 1024  # technical guard for one resumable chunk request, not a plan limit
    # System ceilings: a plan's `limit.file_size_bytes` can lower but never exceed these.
    max_file_size_ceiling_bytes: int = 200 * 1024**2
    max_import_batch_files: int = 200
    upload_session_ttl_seconds: int = 24 * 3600
    # Document intelligence (Phase 7). OCR runs locally by default; cloud providers are opt-in per organization (consent flag).
    ocr_provider: str = "tesseract"
    tesseract_cmd: str = "tesseract"
    ocr_timeout_seconds: int = 120
    ocr_render_dpi: int = 200
    ocr_max_pages: int = 200
    ocr_default_languages: list[str] = Field(default_factory=lambda: ["eng"])
    understanding_provider: str = "rules"  # rules (deterministic, local) | http (OpenAI-compatible JSON API, opt-in)
    ai_http_base_url: str | None = None
    ai_http_model: str | None = None
    ai_http_api_key: SecretStr | None = None  # server-side only; never returned by any API, never logged
    ai_http_timeout_seconds: int = 60
    processing_mode: Literal["celery", "background"] = "background"  # celery in production; in-process otherwise
    processing_retry_backoff_seconds: float = 2.0
    processing_max_attempts: int = 3
    excel_max_template_bytes: int = 10 * 1024 * 1024
    excel_max_export_rows: int = 200_000
    excel_export_max_attempts: int = 3
    # Antivirus: "none" means files are validated but NOT scanned, and the product says so.
    scanner_backend: Literal["none", "clamav"] = "none"
    clamd_host: str = "localhost"
    clamd_port: int = 3310
    clamd_timeout_seconds: float = 30.0

    @property
    def effective_log_format(self) -> str:
        return ("text" if self.environment == "local" else "json") if self.log_format == "auto" else self.log_format

    @model_validator(mode="after")
    def _no_dev_secrets_outside_dev(self) -> Settings:
        if self.environment in ("staging", "production"):
            for name in ("tenant_jwt_secret", "platform_jwt_secret", "mfa_encryption_key"):
                value = getattr(self, name).get_secret_value()
                if _DEV_SECRET_MARKER in value or value.startswith("ZGV2LW9ubHktbWZh"):
                    raise ValueError(f"{name} must be set to a real secret in {self.environment}")
            if self.mailer_backend != "smtp" or not self.smtp_host:
                raise ValueError("mailer_backend must be 'smtp' with smtp_host set in staging/production")
            if not self.web_base_url.startswith("https://"):
                raise ValueError("web_base_url must be https in staging/production")
            if self.storage_backend != "s3" or not self.storage_s3_bucket:
                raise ValueError("storage_backend must be 's3' with storage_s3_bucket set in staging/production")
            if self.rate_limit_backend != "redis":
                raise ValueError("rate_limit_backend must be 'redis' in staging/production")
            for name in ("database_url_app", "database_url_platform", "database_url_dispatcher"):
                if "dev-only" in getattr(self, name):
                    raise ValueError(f"{name} contains a development password")
        if self.mailer_backend == "smtp" and not self.smtp_host:
            raise ValueError("smtp_host is required when mailer_backend is 'smtp'")
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
