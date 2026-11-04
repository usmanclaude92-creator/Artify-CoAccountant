from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text

from artify_api.context import Public
from artify_api.db import app_engine

router = APIRouter(tags=["health"])


@router.get("/health", dependencies=[Depends(Public("liveness probe"))])
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", dependencies=[Depends(Public("readiness probe"))])
def readiness(response: Response) -> dict[str, str]:
    try:
        with app_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        response.status_code = 503
        return {"status": "database_unavailable"}
    return {"status": "ok"}
