"""Channel endpoints: the live manifest and now-playing info.

The manifest is a pure function of wall clock + catalog, so a 1-second
in-process cache (per channel) collapses any number of concurrent viewers into
at most one window computation per second. Routes are parametric over
`channel_id`, so `demo` is simply one channel among many.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Response

from app import config
from app.core.catalog import (
    CatalogNotFound,
    channel_epoch,
    load_catalog,
    load_catalog_from_db,
    load_schedule_from_db,
)
from app.core.manifest import render_media_playlist
from app.core.schedule import ScheduledTimeline
from app.core.timeline import ChannelNotStarted, LoopingTimeline, utcnow

router = APIRouter()

# Per-channel caches. A schedule/catalog edit resets the affected channel.
_timelines: dict[str, object] = {}
_manifest_cache: dict[str, tuple[float, str]] = {}


def reset_timeline(channel_id: str | None = None) -> None:
    """Drop cached timeline(s) so a schedule/catalog edit shows. None = all."""
    if channel_id is None:
        _timelines.clear()
        _manifest_cache.clear()
    else:
        _timelines.pop(channel_id, None)
        _manifest_cache.pop(channel_id, None)


def _build_timeline(channel_id: str):
    # JSON demo catalog (Phases 0-1): a single "demo" channel that loops.
    if config.CATALOG_SOURCE != "db":
        return LoopingTimeline(load_catalog(config.CATALOG_PATH), epoch=config.CHANNEL_EPOCH)

    from app.db import session_scope

    with session_scope() as session:
        assets = load_catalog_from_db(session)  # raises CatalogNotFound if empty
        schedule = load_schedule_from_db(session, {a.id: a for a in assets}, channel_id)
        epoch = channel_epoch(session, channel_id)

    if schedule is not None:
        return ScheduledTimeline(schedule, epoch=epoch)
    # No schedule for this channel: loop the whole catalog.
    return LoopingTimeline(assets, epoch=epoch)


def get_timeline(channel_id: str = "demo"):
    if channel_id not in _timelines:
        try:
            _timelines[channel_id] = _build_timeline(channel_id)
        except CatalogNotFound as e:
            raise HTTPException(status_code=503, detail=str(e))
    return _timelines[channel_id]


@router.get("/channel/{channel_id}/playlist.m3u8")
def playlist(channel_id: str) -> Response:
    now_mono = time.monotonic()
    cached = _manifest_cache.get(channel_id)
    if cached and cached[0] > now_mono:
        body = cached[1]
    else:
        timeline = get_timeline(channel_id)
        try:
            window = timeline.window(utcnow(), size=config.WINDOW_SIZE)
        except ChannelNotStarted as e:
            raise HTTPException(status_code=404, detail=str(e))
        body = render_media_playlist(window)
        _manifest_cache[channel_id] = (now_mono + config.MANIFEST_CACHE_TTL, body)
    return Response(
        content=body,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "max-age=1"},
    )


@router.get("/channel/{channel_id}/now")
def now_playing(channel_id: str) -> dict:
    timeline = get_timeline(channel_id)
    try:
        info = timeline.now_playing(utcnow())
    except ChannelNotStarted as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "on_air": {
            "asset_id": info.asset_id,
            "title": info.asset_title,
            "position_s": round(info.asset_position, 1),
            "duration_s": round(info.asset_duration, 1),
        },
        "up_next": {"asset_id": info.up_next_id, "title": info.up_next_title},
    }
