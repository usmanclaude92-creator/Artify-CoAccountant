"""Private object storage behind one interface (docs/documents/CLOUD_STORAGE_ARCHITECTURE.md).

Binary content never lives in PostgreSQL. Keys are opaque (`t/{org}/c/{company}/o/{object_id}`): they carry no file
or folder names, so renaming or moving a document or folder never touches storage. Objects are private; the API
streams them to authorised callers. There are no public or pre-signed URLs in Phase 6.
"""

from __future__ import annotations

import re
import shutil
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from artify_api.settings import Settings, get_settings

CHUNK = 1024 * 1024
_KEY = re.compile(r"^t/[0-9a-f-]{36}/c/[0-9a-f-]{36}/o/[0-9a-f-]{36}$")


class StorageError(Exception):
    """The object store failed or the object is missing; the caller reports an honest failure."""


def object_key(org_id: uuid.UUID, company_id: uuid.UUID, object_id: uuid.UUID) -> str:
    return f"t/{org_id}/c/{company_id}/o/{object_id}"


def company_prefix(org_id: uuid.UUID, company_id: uuid.UUID) -> str:
    return f"t/{org_id}/c/{company_id}/"


def validate_key(key: str) -> str:
    if not _KEY.match(key):
        raise StorageError("invalid storage key")
    return key


@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    modified: float  # POSIX seconds; reconciliation never treats a young object as an orphan


class StorageBackend(Protocol):
    name: str

    def put_file(self, key: str, path: Path) -> None: ...
    def stat(self, key: str) -> int | None: ...
    def open_range(self, key: str, start: int = 0, end: int | None = None) -> Iterator[bytes]: ...
    def delete(self, key: str) -> None: ...
    def list_objects(self, prefix: str) -> Iterator[StoredObject]: ...


class LocalBackend:
    """Filesystem backend for development and tests. NOT for production (the settings validator refuses it)."""

    name = "local"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        validate_key(key)
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise StorageError("storage key escapes the storage root")
        return p

    def put_file(self, key: str, path: Path) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        shutil.copyfile(path, tmp)
        tmp.replace(dest)

    def stat(self, key: str) -> int | None:
        p = self._path(key)
        return p.stat().st_size if p.is_file() else None

    def open_range(self, key: str, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        p = self._path(key)
        if not p.is_file():
            raise StorageError("object not found")

        def gen() -> Iterator[bytes]:
            remaining = None if end is None else end - start + 1
            with p.open("rb") as f:
                f.seek(start)
                while remaining is None or remaining > 0:
                    chunk = f.read(CHUNK if remaining is None else min(CHUNK, remaining))
                    if not chunk:
                        break
                    if remaining is not None:
                        remaining -= len(chunk)
                    yield chunk

        return gen()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def list_objects(self, prefix: str) -> Iterator[StoredObject]:
        base = self.root / prefix
        if not base.exists():
            return
        for p in base.rglob("*"):
            if p.is_file() and not p.name.endswith(".part"):
                st = p.stat()
                yield StoredObject(p.relative_to(self.root).as_posix(), st.st_size, st.st_mtime)


class S3Backend:
    """S3-compatible backend (AWS S3, MinIO, R2). Credentials come from settings or the SDK credential chain."""

    name = "s3"

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        if not settings.storage_s3_bucket:
            raise StorageError("storage_s3_bucket is not configured")
        self.bucket = settings.storage_s3_bucket
        self.sse = settings.storage_s3_sse
        if client is None:
            import boto3

            kwargs: dict[str, Any] = {}
            if settings.storage_s3_endpoint_url:
                kwargs["endpoint_url"] = settings.storage_s3_endpoint_url
            if settings.storage_s3_region:
                kwargs["region_name"] = settings.storage_s3_region
            if settings.storage_s3_access_key_id and settings.storage_s3_secret_access_key:
                kwargs["aws_access_key_id"] = settings.storage_s3_access_key_id.get_secret_value()
                kwargs["aws_secret_access_key"] = settings.storage_s3_secret_access_key.get_secret_value()
            client = boto3.client("s3", **kwargs)
        self.client = client

    def put_file(self, key: str, path: Path) -> None:
        validate_key(key)
        extra: dict[str, str] = {"ContentType": "application/octet-stream"}
        if self.sse:
            extra["ServerSideEncryption"] = self.sse
        try:
            self.client.upload_file(str(path), self.bucket, key, ExtraArgs=extra)
        except Exception as exc:  # boto3 raises many types; callers treat any failure as "not stored"
            raise StorageError(f"upload failed: {type(exc).__name__}") from exc

    def stat(self, key: str) -> int | None:
        validate_key(key)
        try:
            return int(self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return None
            raise StorageError(f"stat failed: {type(exc).__name__}") from exc

    def open_range(self, key: str, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        validate_key(key)
        rng = f"bytes={start}-{'' if end is None else end}"
        try:
            body = self.client.get_object(Bucket=self.bucket, Key=key, Range=rng)["Body"]
        except Exception as exc:
            raise StorageError(f"download failed: {type(exc).__name__}") from exc

        def gen() -> Iterator[bytes]:
            try:
                yield from body.iter_chunks(CHUNK)
            finally:
                body.close()

        return gen()

    def delete(self, key: str) -> None:
        validate_key(key)
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise StorageError(f"delete failed: {type(exc).__name__}") from exc

    def list_objects(self, prefix: str) -> Iterator[StoredObject]:
        pager = self.client.get_paginator("list_objects_v2")
        for page in pager.paginate(Bucket=self.bucket, Prefix=prefix):
            for o in page.get("Contents", []):
                yield StoredObject(o["Key"], int(o["Size"]), o["LastModified"].timestamp())


_backend: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _backend
    if _backend is None:
        s = get_settings()
        _backend = S3Backend(s) if s.storage_backend == "s3" else LocalBackend(s.storage_local_root)
    return _backend


def set_storage(backend: StorageBackend | None) -> None:
    """Test hook: install (or clear) the process-wide backend."""
    global _backend
    _backend = backend
