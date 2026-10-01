"""Channel + schedule management.

Schedules are per-channel. `PUT /channels/{id}/schedule` (and the legacy
`/schedule`, which targets `demo`) replaces a channel's schedule atomically,
validating with the shared `validate_schedule` rules and rejecting (422) before
persisting anything, so a channel never airs an overlapping or out-of-bounds
programme. The AI director uses the same write path.
"""

from __future__ import annotations

from datetime import datetime

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


class ChannelIn(BaseModel):
    id: str
    name: str | None = None
    epoch: str | None = None  # ISO 8601; defaults to the global epoch


class GenerateIn(BaseModel):
    brief: str
    period_seconds: float = Field(default=3600, gt=0)


def _catalog(session) -> dict:
    try:
        assets = load_catalog_from_db(session)
    except CatalogNotFound:
        assets = []
    return {a.id: a for a in assets}


def _write_schedule(session, channel_id, filler_asset_id, period_seconds, entries, ad_breaks) -> None:
    """Persist a channel's schedule atomically. `entries`: [(asset_id, offset)];
    `ad_breaks`: [(offset, duration)]. Shared by PUT and the AI director."""
    session.query(ScheduleEntryRow).filter(
        ScheduleEntryRow.channel_id == channel_id
    ).delete()
    session.query(AdBreakRow).filter(AdBreakRow.channel_id == channel_id).delete()
    settings = session.get(ChannelSettingsRow, channel_id) or ChannelSettingsRow(id=channel_id)
    settings.filler_asset_id = filler_asset_id
    settings.period_seconds = period_seconds
    session.add(settings)
    for asset_id, offset in entries:
        session.add(
            ScheduleEntryRow(channel_id=channel_id, asset_id=asset_id, start_offset=offset)
        )
    for offset, duration in ad_breaks:
        session.add(
            AdBreakRow(channel_id=channel_id, start_offset=offset, duration=duration)
        )


def _get_schedule(session, channel_id) -> dict:
    settings = session.get(ChannelSettingsRow, channel_id)
    rows = (
        session.query(ScheduleEntryRow)
        .filter(ScheduleEntryRow.channel_id == channel_id)
        .order_by(ScheduleEntryRow.start_offset)
        .all()
    )
    catalog = _catalog(session)
    ad_breaks = (
        session.query(AdBreakRow)
        .filter(AdBreakRow.channel_id == channel_id)
        .order_by(AdBreakRow.start_offset)
        .all()
    )
    return {
        "channel_id": channel_id,
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
            {"start_offset": a.start_offset, "duration": a.duration} for a in ad_breaks
        ],
    }


def _put_schedule(channel_id: str, body: ScheduleIn) -> dict:
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
            channel_id,
            body.filler_asset_id,
            body.period_seconds,
            [(e.asset_id, e.start_offset) for e in body.entries],
            [(a.start_offset, a.duration) for a in body.ad_breaks],
        )
    channel.reset_timeline(channel_id)
    return {"status": "ok", "entries": len(body.entries), "ad_breaks": len(body.ad_breaks)}


def _generate(channel_id: str, body: GenerateIn) -> dict:
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
            channel_id,
            result["filler_asset_id"],
            result["period_seconds"],
            [(e["asset_id"], e["start_offset"]) for e in result["entries"]],
            [(a["start_offset"], a["duration"]) for a in result["ad_breaks"]],
        )
    channel.reset_timeline(channel_id)
    return {"status": "ok", "channel_id": channel_id, **result}


def _clear(channel_id: str) -> dict:
    with session_scope() as s:
        s.query(ScheduleEntryRow).filter(
            ScheduleEntryRow.channel_id == channel_id
        ).delete()
        s.query(AdBreakRow).filter(AdBreakRow.channel_id == channel_id).delete()
        settings = s.get(ChannelSettingsRow, channel_id)
        if settings is not None:
            settings.filler_asset_id = None
    channel.reset_timeline(channel_id)
    return {"status": "ok"}


# --- channel management ---------------------------------------------------


@router.get("/channels")
def list_channels() -> list[dict]:
    with session_scope() as s:
        out = []
        for row in s.query(ChannelSettingsRow).order_by(ChannelSettingsRow.id):
            entries = (
                s.query(ScheduleEntryRow)
                .filter(ScheduleEntryRow.channel_id == row.id)
                .count()
            )
            out.append(
                {
                    "id": row.id,
                    "name": row.name,
                    "epoch": row.epoch,
                    "has_schedule": entries > 0,
                }
            )
        return out


@router.post("/channels", status_code=201)
def create_channel(body: ChannelIn) -> dict:
    if body.epoch is not None:
        try:
            datetime.fromisoformat(body.epoch)
        except ValueError:
            raise HTTPException(status_code=422, detail="epoch must be ISO 8601")
    with session_scope() as s:
        if s.get(ChannelSettingsRow, body.id) is not None:
            raise HTTPException(status_code=409, detail=f"channel '{body.id}' exists")
        s.add(ChannelSettingsRow(id=body.id, name=body.name, epoch=body.epoch))
    return {"status": "ok", "id": body.id}


@router.delete("/channels/{channel_id}")
def delete_channel(channel_id: str) -> dict:
    _clear(channel_id)
    with session_scope() as s:
        settings = s.get(ChannelSettingsRow, channel_id)
        if settings is not None:
            s.delete(settings)
    channel.reset_timeline(channel_id)
    return {"status": "ok"}


# --- per-channel schedule -------------------------------------------------


@router.get("/channels/{channel_id}/schedule")
def get_channel_schedule(channel_id: str) -> dict:
    with session_scope() as s:
        return _get_schedule(s, channel_id)


@router.put("/channels/{channel_id}/schedule")
def put_channel_schedule(channel_id: str, body: ScheduleIn) -> dict:
    return _put_schedule(channel_id, body)


@router.delete("/channels/{channel_id}/schedule")
def clear_channel_schedule(channel_id: str) -> dict:
    return _clear(channel_id)


@router.post("/channels/{channel_id}/schedule/generate")
def generate_channel_schedule(channel_id: str, body: GenerateIn) -> dict:
    return _generate(channel_id, body)


# --- legacy single-channel routes (operate on "demo") ---------------------


@router.get("/schedule")
def get_schedule() -> dict:
    with session_scope() as s:
        return _get_schedule(s, "demo")


@router.put("/schedule")
def put_schedule(body: ScheduleIn) -> dict:
    return _put_schedule("demo", body)


@router.post("/schedule/generate")
def generate_schedule(body: GenerateIn) -> dict:
    return _generate("demo", body)


@router.delete("/schedule")
def clear_schedule() -> dict:
    return _clear("demo")
