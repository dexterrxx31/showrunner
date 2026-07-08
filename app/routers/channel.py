"""Channel endpoints: the live manifest and now-playing info.

The manifest is a pure function of wall clock + catalog, so a 1-second
in-process cache collapses any number of concurrent viewers into at most
one window computation per second.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Response

from app import config
from app.core.catalog import CatalogNotFound, load_catalog
from app.core.manifest import render_media_playlist
from app.core.timeline import ChannelNotStarted, LoopingTimeline, utcnow

router = APIRouter()

_timeline: LoopingTimeline | None = None
_manifest_cache: tuple[float, str] | None = None


def get_timeline() -> LoopingTimeline:
    global _timeline
    if _timeline is None:
        try:
            assets = load_catalog(config.CATALOG_PATH)
        except CatalogNotFound as e:
            raise HTTPException(status_code=503, detail=str(e))
        _timeline = LoopingTimeline(assets, epoch=config.CHANNEL_EPOCH)
    return _timeline


@router.get("/channel/demo/playlist.m3u8")
def playlist() -> Response:
    global _manifest_cache
    now_mono = time.monotonic()
    if _manifest_cache and _manifest_cache[0] > now_mono:
        body = _manifest_cache[1]
    else:
        timeline = get_timeline()
        try:
            window = timeline.window(utcnow(), size=config.WINDOW_SIZE)
        except ChannelNotStarted as e:
            raise HTTPException(status_code=404, detail=str(e))
        body = render_media_playlist(window)
        _manifest_cache = (now_mono + config.MANIFEST_CACHE_TTL, body)
    return Response(
        content=body,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "max-age=1"},
    )


@router.get("/channel/demo/now")
def now_playing() -> dict:
    timeline = get_timeline()
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
