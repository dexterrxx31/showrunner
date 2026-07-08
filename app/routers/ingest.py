"""Ingest endpoints: upload a source file, track normalization progress."""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, UploadFile

from app import config
from app.db import session_scope
from app.models import IngestJobRow

router = APIRouter()


def _slug(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return f"{base or 'asset'}-{uuid.uuid4().hex[:8]}"


@router.post("/ingest", status_code=202)
async def ingest(
    background: BackgroundTasks,
    file: UploadFile,
    title: str = Form(...),
) -> dict:
    job_id = uuid.uuid4().hex
    asset_id = _slug(title)

    upload_dir = Path(config.UPLOAD_DIR) / job_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored = upload_dir / (file.filename or "source")
    with stored.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    with session_scope() as s:
        s.add(IngestJobRow(id=job_id, asset_id=asset_id, title=title, status="pending"))

    if config.CELERY_BROKER_URL:
        from app.ingest.tasks import ingest_task

        ingest_task.delay(job_id, str(stored), asset_id, title)
    else:
        from app.ingest.jobs import run_ingest_job

        background.add_task(run_ingest_job, job_id, str(stored), asset_id, title)

    return {"job_id": job_id, "asset_id": asset_id, "status": "pending"}


@router.get("/ingest/{job_id}")
def ingest_status(job_id: str) -> dict:
    with session_scope() as s:
        job = s.get(IngestJobRow, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        return {
            "job_id": job.id,
            "asset_id": job.asset_id,
            "title": job.title,
            "status": job.status,
            "segment_count": job.segment_count,
            "error": job.error,
        }
