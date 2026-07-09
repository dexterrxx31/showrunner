"""EPG endpoints: XMLTV for PVRs/apps, JSON for the demo UI. Per channel."""

from __future__ import annotations

from fastapi import APIRouter, Query, Response

from app.core.epg import build_epg_json, build_xmltv
from app.core.timeline import utcnow
from app.routers.channel import get_timeline

router = APIRouter()


@router.get("/channel/{channel_id}/epg.xml")
def channel_epg_xml(channel_id: str, hours: float = Query(6, gt=0, le=168)) -> Response:
    xml = build_xmltv(get_timeline(channel_id), utcnow(), hours, channel_id=channel_id)
    return Response(content=xml, media_type="application/xml")


@router.get("/channel/{channel_id}/epg.json")
def channel_epg_json(channel_id: str, hours: float = Query(6, gt=0, le=168)) -> list[dict]:
    return build_epg_json(get_timeline(channel_id), utcnow(), hours)


# Legacy single-channel aliases (demo).
@router.get("/epg.xml")
def epg_xml(hours: float = Query(6, gt=0, le=168)) -> Response:
    xml = build_xmltv(get_timeline("demo"), utcnow(), hours)
    return Response(content=xml, media_type="application/xml")


@router.get("/epg.json")
def epg_json(hours: float = Query(6, gt=0, le=168)) -> list[dict]:
    return build_epg_json(get_timeline("demo"), utcnow(), hours)
