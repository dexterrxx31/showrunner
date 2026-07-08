"""EPG endpoints: XMLTV for PVRs/apps, JSON for the demo UI."""

from __future__ import annotations

from fastapi import APIRouter, Query, Response

from app.core.epg import build_epg_json, build_xmltv
from app.core.timeline import utcnow
from app.routers.channel import get_timeline

router = APIRouter()


@router.get("/epg.xml")
def epg_xml(hours: float = Query(6, gt=0, le=168)) -> Response:
    timeline = get_timeline()
    xml = build_xmltv(timeline, utcnow(), hours)
    return Response(content=xml, media_type="application/xml")


@router.get("/epg.json")
def epg_json(hours: float = Query(6, gt=0, le=168)) -> list[dict]:
    return build_epg_json(get_timeline(), utcnow(), hours)
