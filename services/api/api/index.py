"""Vercel entry point (ASGI). The first request of a new instance makes sure the database is migrated, the login roles
exist and reference data is seeded (idempotent, serialised by an advisory lock); then every request goes to the app.
Only used for the hosted demo/preview deployment; production runs the container image (docs/deployment/VERCEL_DEMO.md)."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from artify_api.main import app as _app

_state: dict[str, object] = {"done": False, "error": None}
_lock = asyncio.Lock()


async def app(scope, receive, send):  # type: ignore[no-untyped-def]
    if scope["type"] == "http" and not _state["done"] and os.environ.get("ARTIFY_AUTO_BOOTSTRAP") == "1":
        async with _lock:
            if not _state["done"]:
                from scripts.bootstrap_remote import ensure_bootstrapped

                try:
                    await asyncio.to_thread(ensure_bootstrapped)
                    _state["done"] = True
                except Exception as exc:  # report once, retry on the next request
                    _state["error"] = f"{type(exc).__name__}: {exc}"[:500]
                    body = b'{"code":"bootstrap_failed","detail":"The hosted database is not ready yet."}'
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 503,
                            "headers": [(b"content-type", b"application/json")],
                        }
                    )
                    await send({"type": "http.response.body", "body": body})
                    print("bootstrap failed:", _state["error"], flush=True)
                    return
    await _app(scope, receive, send)
