"""Outbox event handling (Phase 12): the Celery task that receives dispatched outbox events, and the function that delivers
them (notifications and e-mail). Kept apart from `worker` so the worker module stays a thin Celery/tenant-context helper."""

from __future__ import annotations

from typing import Any

from artify_api import notifications
from artify_api.worker import celery_app, run_in_tenant_context


def handle_event(event: dict[str, Any]) -> str:
    """Deliver one outbox event: notification rows and e-mail for the event types that have a human recipient (Phase 12).
    Event types without a handler are acknowledged only, as before. Redelivery is safe (one row per event and person)."""
    if event.get("event_type") in notifications.SPECS and event.get("organization_id"):
        res = run_in_tenant_context(
            str(event["organization_id"]),
            str(event["company_id"]) if event.get("company_id") else None,
            lambda conn: notifications.handle_event(conn, event),
        )
        return f"notified:{event.get('event_type')}:{res['created']}"
    return f"handled:{event.get('event_type')}"


@celery_app.task(name="artify.process_event")  # type: ignore[untyped-decorator]
def process_event(event: dict[str, Any]) -> str:
    return handle_event(event)
