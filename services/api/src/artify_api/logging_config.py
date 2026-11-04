"""Structured (JSON) logging with a per-request correlation id.

Never log request bodies, tokens, passwords or financial values; log identifiers and outcomes.
Database errors are logged without bound parameters (engines use hide_parameters=True), so
exception text cannot carry user data."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)

_STANDARD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        cid = correlation_id_var.get()
        if cid:
            payload["correlation_id"] = cid
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = {
                "type": record.exc_info[0].__name__,
                "trace": self.formatException(record.exc_info),
            }
        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                payload[key] = value if isinstance(value, str | int | float | bool | type(None)) else str(value)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(fmt: str, level: str = "INFO") -> None:
    """fmt: 'json' or 'text'. Replaces root handlers so output is uniform across libraries."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        JsonFormatter() if fmt == "json" else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("uvicorn.access",):  # access lines are emitted by our middleware with the correlation id
        logging.getLogger(noisy).handlers[:] = []
        logging.getLogger(noisy).propagate = False
