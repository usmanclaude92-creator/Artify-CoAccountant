"""Identifier helpers. Primary keys are application-generated UUIDv7 (time-ordered)."""

from __future__ import annotations

import uuid

import uuid_utils


def new_id() -> uuid.UUID:
    """Return a new UUIDv7 as a stdlib UUID."""
    return uuid.UUID(bytes=uuid_utils.uuid7().bytes)
