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


def _write_schedule(session, filler_asset_id, period_seconds, entries, ad_breaks) -> None:
    """Persist a schedule atomically. `entries`: [(asset_id, offset)];
    `ad_breaks`: [(offset, duration)]. Shared by PUT and the AI director."""
    session.query(ScheduleEntryRow).delete()
    session.query(AdBreakRow).delete()
    settings = session.get(ChannelSettingsRow, "demo") or ChannelSettingsRow(id="demo")
    settings.filler_asset_id = filler_asset_id
    settings.period_seconds = period_seconds
    session.add(settings)
    for asset_id, offset in entries:
        session.add(ScheduleEntryRow(asset_id=asset_id, start_offset=offset))
    for offset, duration in ad_breaks:
        session.add(AdBreakRow(start_offset=offset, duration=duration))


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

        _write_schedule(
            s,
            body.filler_asset_id,
            body.period_seconds,
            [(e.asset_id, e.start_offset) for e in body.entries],
            [(a.start_offset, a.duration) for a in body.ad_breaks],
        )

    channel.reset_timeline()
    return {"status": "ok", "entries": len(body.entries), "ad_breaks": len(body.ad_breaks)}


class GenerateIn(BaseModel):
    brief: str
    period_seconds: float = Field(default=3600, gt=0)


@router.post("/schedule/generate")
def generate_schedule(body: GenerateIn) -> dict:
    from app.ai import director

    try:
        client = director.make_client()
    except director.DirectorUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))

    with session_scope() as s:
        try:
            result = director.build_schedule(body.brief, body.period_seconds, s, client)
        except director.DirectorError as e:
            raise HTTPException(status_code=422, detail=str(e))
        _write_schedule(
            s,
            result["filler_asset_id"],
            result["period_seconds"],
            [(e["asset_id"], e["start_offset"]) for e in result["entries"]],
            [(a["start_offset"], a["duration"]) for a in result["ad_breaks"]],
        )

    channel.reset_timeline()
    return {"status": "ok", **result}


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
