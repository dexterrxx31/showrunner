"""Schedule CRUD.

`PUT /schedule` replaces the whole schedule atomically — the same shape the
Phase-6 AI director will submit. It validates with the shared
`validate_schedule` rules and rejects (422) before persisting anything, so the
channel never ends up airing an overlapping or out-of-bounds programme.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import app.routers.channel as channel
from app.core.catalog import CatalogNotFound, load_catalog_from_db
from app.core.schedule import validate_schedule
from app.db import session_scope
from app.models import AdBreakRow, ChannelSettingsRow, ScheduleEntryRow

router = APIRouter()


class EntryIn(BaseModel):
    asset_id: str
    start_offset: float = Field(ge=0)


class AdBreakIn(BaseModel):
    start_offset: float = Field(ge=0)
    duration: float = Field(gt=0)


class ScheduleIn(BaseModel):
    filler_asset_id: str
    period_seconds: float = Field(gt=0)
    entries: list[EntryIn]
    ad_breaks: list[AdBreakIn] = []


def _catalog(session) -> dict:
    try:
        assets = load_catalog_from_db(session)
    except CatalogNotFound:
        assets = []
    return {a.id: a for a in assets}


@router.get("/schedule")
def get_schedule() -> dict:
    with session_scope() as s:
        settings = s.get(ChannelSettingsRow, "demo")
        rows = (
            s.query(ScheduleEntryRow)
            .order_by(ScheduleEntryRow.start_offset)
            .all()
        )
        catalog = _catalog(s)
        ad_breaks = s.query(AdBreakRow).order_by(AdBreakRow.start_offset).all()
        return {
            "filler_asset_id": settings.filler_asset_id if settings else None,
            "period_seconds": settings.period_seconds if settings else None,
            "entries": [
                {
                    "asset_id": r.asset_id,
                    "title": catalog[r.asset_id].title if r.asset_id in catalog else None,
                    "start_offset": r.start_offset,
                }
                for r in rows
            ],
            "ad_breaks": [
                {"start_offset": a.start_offset, "duration": a.duration}
                for a in ad_breaks
            ],
        }


@router.put("/schedule")
def put_schedule(body: ScheduleIn) -> dict:
    with session_scope() as s:
        catalog = _catalog(s)

        missing = [e.asset_id for e in body.entries if e.asset_id not in catalog]
        if body.filler_asset_id not in catalog:
            missing.append(body.filler_asset_id)
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"unknown asset id(s): {', '.join(sorted(set(missing)))}",
            )

        entries = [(catalog[e.asset_id], e.start_offset) for e in body.entries]
        ad_breaks = [(a.start_offset, a.duration) for a in body.ad_breaks]
        errors = validate_schedule(
            entries, catalog[body.filler_asset_id], body.period_seconds, ad_breaks
        )
        if errors:
            raise HTTPException(status_code=422, detail=errors)

        s.query(ScheduleEntryRow).delete()
        s.query(AdBreakRow).delete()
        settings = s.get(ChannelSettingsRow, "demo")
        if settings is None:
            settings = ChannelSettingsRow(id="demo")
            s.add(settings)
        settings.filler_asset_id = body.filler_asset_id
        settings.period_seconds = body.period_seconds
        for e in body.entries:
            s.add(ScheduleEntryRow(asset_id=e.asset_id, start_offset=e.start_offset))
        for a in body.ad_breaks:
            s.add(AdBreakRow(start_offset=a.start_offset, duration=a.duration))

    channel.reset_timeline()
    return {"status": "ok", "entries": len(body.entries), "ad_breaks": len(body.ad_breaks)}


@router.delete("/schedule")
def clear_schedule() -> dict:
    with session_scope() as s:
        s.query(ScheduleEntryRow).delete()
        s.query(AdBreakRow).delete()
        settings = s.get(ChannelSettingsRow, "demo")
        if settings is not None:
            settings.filler_asset_id = None
    channel.reset_timeline()
    return {"status": "ok"}
