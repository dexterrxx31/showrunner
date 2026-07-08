"""Celery task wrapper for ingest.

Only used when SHOWRUNNER_BROKER is configured; otherwise the router runs
`run_ingest_job` directly via FastAPI background tasks. Import is lazy-safe:
Celery is a declared dependency, but a missing broker just means eager mode.
"""

from __future__ import annotations

from celery import Celery

from app import config
from app.ingest.jobs import run_ingest_job

celery_app = Celery(
    "showrunner",
    broker=config.CELERY_BROKER_URL or "memory://",
    backend="cache+memory://",
)


@celery_app.task(name="ingest_asset")
def ingest_task(job_id: str, input_path: str, asset_id: str, title: str) -> None:
    run_ingest_job(job_id, input_path, asset_id, title)
