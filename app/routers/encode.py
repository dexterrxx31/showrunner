"""True-encode endpoints: start/stop a continuous branded FFmpeg playout.

Optional alternative to the stateless manifest origin. Builds the channel's
cycle (via the same snapshot the Go origin uses), points FFmpeg at those
segments on a loop with a branding overlay, and serves the re-encoded stream
under /encoded/{channel_id}/playlist.m3u8.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

import app.routers.channel as channel
from app import config
from app.core.catalog import CatalogNotFound
from app.core.encode import (
    build_encode_cmd,
    encoder,
    ffmpeg_has_drawtext,
    find_default_font,
    write_concat_file,
)
from app.core.snapshot import export_snapshot
from app.db import session_scope
from app.models import ChannelSettingsRow

router = APIRouter()


def _local_segment_path(uri: str) -> Path:
    # "/segments/asset/00000.ts" -> <SEGMENT_DIR>/asset/00000.ts
    return Path(config.SEGMENT_DIR) / uri.removeprefix("/segments/")


def _channel_name(channel_id: str) -> str:
    if config.CATALOG_SOURCE == "db":
        with session_scope() as s:
            row = s.get(ChannelSettingsRow, channel_id)
            if row is not None and row.name:
                return row.name
    return channel_id.upper()


def _playlist_url(channel_id: str) -> str:
    return f"/encoded/{channel_id}/playlist.m3u8"


@router.post("/channel/{channel_id}/encode/start")
def start_encode(channel_id: str) -> dict:
    try:
        timeline = channel._build_timeline(channel_id)
    except CatalogNotFound as e:
        raise HTTPException(status_code=503, detail=str(e))

    snapshot = export_snapshot(timeline)
    paths = [_local_segment_path(s["uri"]) for s in snapshot["segments"]]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise HTTPException(
            status_code=409,
            detail="segment files not found locally (true-encode needs local "
            f"segment storage): {missing[0]}",
        )

    out_dir = Path(config.ENCODE_DIR) / channel_id
    out_dir.mkdir(parents=True, exist_ok=True)
    concat = write_concat_file(paths, out_dir / "concat.txt")
    # Burn in the channel name only if this ffmpeg build supports drawtext;
    # otherwise degrade to the bar-only overlay so the encode still runs.
    font = (config.ENCODE_FONT or find_default_font()) if ffmpeg_has_drawtext() else None
    cmd = build_encode_cmd(
        concat,
        out_dir,
        channel_name=_channel_name(channel_id),
        fontfile=font,
        segment_seconds=config.SEGMENT_SECONDS,
    )
    encoder.start(channel_id, cmd)
    return {"status": "started", "playlist_url": _playlist_url(channel_id)}


@router.post("/channel/{channel_id}/encode/stop")
def stop_encode(channel_id: str) -> dict:
    stopped = encoder.stop(channel_id)
    return {"status": "stopped" if stopped else "not_running"}


@router.get("/channel/{channel_id}/encode/status")
def encode_status(channel_id: str) -> dict:
    running = encoder.is_running(channel_id)
    return {
        "running": running,
        "playlist_url": _playlist_url(channel_id) if running else None,
    }
