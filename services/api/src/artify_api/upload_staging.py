"""Staging files for resumable uploads (Phase 12). One append-only file per upload session in the staging directory.

The database column `received_bytes` is the authority: a file longer than it (a crash between the write and the commit) is
cut back; a file shorter than it (lost staging volume) lowers the figure so the client re-sends from the real offset. In a
multi-replica deployment the staging directory must be a volume all API replicas share; otherwise a resume that lands on
another replica finds no file and the client simply starts again from zero - safe, never corrupt."""

from __future__ import annotations

import contextlib
import hashlib
import tempfile
import uuid
from pathlib import Path

from artify_api.settings import get_settings


def path_for(op_id: uuid.UUID) -> Path:
    base = Path(get_settings().storage_tmp_dir or tempfile.gettempdir())
    base.mkdir(parents=True, exist_ok=True)
    return base / f"artify-upload-{op_id}.part"


def size(op_id: uuid.UUID) -> int:
    p = path_for(op_id)
    return p.stat().st_size if p.exists() else 0


def truncate(op_id: uuid.UUID, length: int) -> None:
    p = path_for(op_id)
    if p.exists():
        with p.open("r+b") as f:
            f.truncate(length)


def discard(op_id: uuid.UUID) -> None:
    with contextlib.suppress(OSError):
        path_for(op_id).unlink()


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()
