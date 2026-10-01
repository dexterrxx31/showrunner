"""Run an ingest job and track its status.

Shared by both execution paths: FastAPI background tasks (eager, dev/test) and
the Celery worker (production). Either way the status lives in the database, so
`GET /ingest/{id}` works regardless of which path ran the job.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app import config
from app.db import session_scope
from app.ingest.pipeline import ingest_asset
from app.models import AssetRow, IngestJobRow
from app.storage import get_store

logger = logging.getLogger(__name__)


def _set_status(job_id: str, status: str, *, error: str | None = None, segment_count: int | None = None) -> None:
    with session_scope() as s:
        job = s.get(IngestJobRow, job_id)
        if job is None:
            return
        job.status = status
        if error is not None:
            job.error = error[:2000]
        if segment_count is not None:
            job.segment_count = segment_count


def run_ingest_job(job_id: str, input_path: str, asset_id: str, title: str) -> None:
    _set_status(job_id, "running")
    try:
        with session_scope() as s:
            position = s.query(AssetRow).count()
            asset = ingest_asset(
                input_path=input_path,
                asset_id=asset_id,
                title=title,
                store=get_store(),
                session=s,
                workdir=config.WORK_DIR,
                position=position,
            )
        _set_status(job_id, "done", segment_count=len(asset.segments))
        _invalidate_channel()
    except Exception as e:  # noqa: BLE001  # record any failure for the caller
        logger.exception("ingest job %s failed", job_id)
        _set_status(job_id, "error", error=str(e))
    finally:
        _cleanup(input_path)


def _invalidate_channel() -> None:
    # Same-process eager path: drop the cached timeline so the new asset airs.
    # Distributed workers can't reach the API's memory, so this is best effort
    # and a failure must not fail an otherwise successful ingest.
    try:
        import app.routers.channel as channel

        channel.reset_timeline()
    except Exception:
        logger.warning("timeline cache invalidation failed", exc_info=True)


def _cleanup(input_path: str) -> None:
    try:
        p = Path(input_path)
        if p.exists():
            p.unlink()
    except OSError:
        pass
