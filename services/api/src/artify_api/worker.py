"""Celery wiring. Tasks never trust ambient state: they carry organization_id/company_id in the
payload and re-establish the RLS context before touching data (ADR-018)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from celery import Celery
from sqlalchemy import Connection

from artify_api.db import DbContext, app_engine, transaction
from artify_api.settings import get_settings

celery_app = Celery("artify", broker=get_settings().redis_url, backend=None)
celery_app.conf.task_acks_late = True
celery_app.conf.task_reject_on_worker_lost = True


def run_in_tenant_context(org_id: str, company_id: str | None, fn: Callable[[Connection], Any]) -> Any:
    """Run `fn` with RLS limited to one Organization (and one Company when given). Workers act as
    the `system` actor: they get no user identity and therefore can never satisfy an approval."""
    org = uuid.UUID(org_id)
    ctx = DbContext(
        user_id=None,
        org_id=org,
        company_scope="list" if company_id else "none",
        company_ids=[uuid.UUID(company_id)] if company_id else [],
    )
    with transaction(app_engine(), ctx) as conn:
        return fn(conn)


@celery_app.task(name="artify.process_event")  # type: ignore[untyped-decorator]
def process_event(event: dict[str, Any]) -> str:
    """Phase 1 skeleton: acknowledges events. Real handlers arrive with the features that emit them."""
    return f"handled:{event.get('event_type')}"
