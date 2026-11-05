"""Celery entry point for document processing. Kept separate so `worker.py` stays a low-level module."""

from __future__ import annotations

from artify_api import processing
from artify_api.worker import celery_app


@celery_app.task(name="artify.process_document", acks_late=True)  # type: ignore[untyped-decorator]
def process_document(org_id: str, company_id: str, job_id: str) -> str:
    """The payload carries ids only; the job is re-read under row-level security for that organization/company."""
    return processing.run_job(org_id, company_id, job_id)
