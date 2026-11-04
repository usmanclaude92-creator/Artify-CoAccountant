"""Private object storage for document binaries.

Rules (docs/documents/CLOUD_STORAGE_ARCHITECTURE.md):
- keys are opaque (`o/<org>/<company>/<random>`): never a filename, a folder name or any user-controlled text, so
  renaming or moving a workspace node never touches an object;
- there is no public URL: bytes leave only through the API (authorized streaming) or a short-lived signed grant that
  the API itself serves;
- an object is written to a staging name and moved into place only after the API has verified size, checksum and content,
  so a reader can never observe a half-written object;
- the driver is a narrow protocol so an S3/MinIO adapter can be added without touching callers (NOT implemented yet).
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO, Protocol

from artify_api.settings import get_settings

_KEY_RE = re.compile(r"^o/[0-9a-f]{32}/[0-9a-f]{32}/[0-9a-f]{32}$")
_STAGE_RE = re.compile(r"^[0-9a-f]{32}$")
CHUNK = 1024 * 1024


class StorageError(Exception):
    """The storage backend could not complete an operation (never contains file content)."""


class ObjectStorage(Protocol):
    name: str

    def new_key(self, org_id: uuid.UUID, company_id: uuid.UUID) -> str: ...
    def stage_append(self, stage_ref: str, offset: int, data: bytes) -> int: ...
    def stage_size(self, stage_ref: str) -> int: ...
    def stage_open(self, stage_ref: str) -> BinaryIO: ...
    def stage_discard(self, stage_ref: str) -> None: ...
    def commit_stage(self, stage_ref: str, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...
    def size(self, key: str) -> int: ...
    def read(self, key: str, start: int = 0, end: int | None = None) -> Iterator[bytes]: ...
    def delete(self, key: str) -> None: ...
    def list_keys(self) -> Iterator[str]: ...
    def list_stages(self) -> Iterator[tuple[str, float]]: ...


class FilesystemStorage:
    """Objects on a private local/mounted directory (mode 0700). Production: an encrypted volume."""

    name = "filesystem"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self._objects = self.root / "objects"
        self._staging = self.root / "staging"
        for d in (self.root, self._objects, self._staging):
            d.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(OSError):  # platform dependent
                d.chmod(0o700)

    # -- key hygiene: a path is built only from a key that matches the strict opaque pattern
    def _path(self, key: str) -> Path:
        if not _KEY_RE.fullmatch(key):
            raise StorageError("invalid storage key")
        p = (self._objects / key[2:]).resolve()
        if self._objects not in p.parents:  # pragma: no cover - the regex already forbids it
            raise StorageError("invalid storage key")
        return p

    def _stage(self, ref: str) -> Path:
        if not _STAGE_RE.fullmatch(ref):
            raise StorageError("invalid staging reference")
        return self._staging / ref

    def new_key(self, org_id: uuid.UUID, company_id: uuid.UUID) -> str:
        return f"o/{org_id.hex}/{company_id.hex}/{uuid.uuid4().hex}"

    def stage_append(self, stage_ref: str, offset: int, data: bytes) -> int:
        """Append at exactly `offset` (resumable, never overwrites received bytes). Returns the new size."""
        p = self._stage(stage_ref)
        current = p.stat().st_size if p.exists() else 0
        if offset != current:
            raise StorageError(f"offset mismatch: have {current}")
        with open(p, "ab") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        return current + len(data)

    def stage_size(self, stage_ref: str) -> int:
        p = self._stage(stage_ref)
        return p.stat().st_size if p.exists() else 0

    def stage_open(self, stage_ref: str) -> BinaryIO:
        return open(self._stage(stage_ref), "rb")

    def stage_discard(self, stage_ref: str) -> None:
        self._stage(stage_ref).unlink(missing_ok=True)

    def commit_stage(self, stage_ref: str, key: str) -> None:
        src, dst = self._stage(stage_ref), self._path(key)
        if not src.exists():
            raise StorageError("nothing staged")
        if dst.exists():
            raise StorageError("object already exists")  # keys are random; a clash is a bug, never an overwrite
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        os.utime(dst)  # age is measured from the commit, so reconciliation never mistakes a fresh object for an orphan
        with contextlib.suppress(OSError):
            dst.chmod(0o600)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def size(self, key: str) -> int:
        p = self._path(key)
        if not p.is_file():
            raise StorageError("object not found")
        return p.stat().st_size

    def read(self, key: str, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        p = self._path(key)
        if not p.is_file():
            raise StorageError("object not found")
        remaining = None if end is None else end - start + 1
        with open(p, "rb") as fh:
            fh.seek(start)
            while remaining is None or remaining > 0:
                chunk = fh.read(CHUNK if remaining is None else min(CHUNK, remaining))
                if not chunk:
                    break
                if remaining is not None:
                    remaining -= len(chunk)
                yield chunk

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def list_keys(self) -> Iterator[str]:
        for f in self._objects.rglob("*"):
            if f.is_file():
                rel = f.relative_to(self._objects).as_posix()
                yield f"o/{rel}"

    def list_stages(self) -> Iterator[tuple[str, float]]:
        for f in self._staging.iterdir():
            if f.is_file() and _STAGE_RE.fullmatch(f.name):
                yield f.name, f.stat().st_mtime


_storage: ObjectStorage | None = None


def get_storage() -> ObjectStorage:
    global _storage
    if _storage is None:
        s = get_settings()
        if s.storage_backend != "filesystem":  # pragma: no cover - the Literal forbids it today
            raise StorageError("unsupported storage backend")
        _storage = FilesystemStorage(s.storage_root)
    return _storage


def set_storage(storage: ObjectStorage | None) -> None:
    """Tests and the reconcile CLI inject a driver."""
    global _storage
    _storage = storage
