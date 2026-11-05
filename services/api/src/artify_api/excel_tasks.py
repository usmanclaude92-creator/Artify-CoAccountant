"""Celery entry point for Excel exports. Kept separate so `worker.py` stays a low-level module."""

from __future__ import annotations

from artify_api import excel
from artify_api.worker import celery_app


@celery_app.task(name="artify.excel_export", acks_late=True)  # type: ignore[untyped-decorator]
def excel_export(org_id: str, company_id: str, export_id: str) -> str:
    """The payload carries ids only; the export is re-read under row-level security for that organization/company."""
    excel.run_export(org_id, company_id, export_id)
    return export_id
