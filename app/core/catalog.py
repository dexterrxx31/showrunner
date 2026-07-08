"""Load the asset catalog produced at ingest time.

Phase 0 uses a JSON file written by scripts/make_demo_assets.py.
Phase 2 replaces this with Postgres-backed catalog rows; the Asset/Segment
dataclasses are the stable contract.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core.timeline import Asset, Segment


class CatalogNotFound(Exception):
    pass


def load_catalog(path: str | Path) -> list[Asset]:
    p = Path(path)
    if not p.exists():
        raise CatalogNotFound(
            f"no catalog at {p} — run `python scripts/make_demo_assets.py` first"
        )
    raw = json.loads(p.read_text())
    assets = []
    for a in raw["assets"]:
        segments = tuple(
            Segment(uri=s["uri"], duration=float(s["duration"]))
            for s in a["segments"]
        )
        if not segments:
            raise ValueError(f"asset {a['id']} has no segments")
        assets.append(Asset(id=a["id"], title=a["title"], segments=segments))
    return assets


def load_catalog_from_db(session) -> list[Asset]:
    """Build the channel's asset list from ingested catalog rows.

    Order is `position` then `created_at` — a stand-in for real programming
    until the Phase-3 scheduler owns ordering. Assets with no segments are
    skipped (an ingest that failed mid-way leaves nothing playable).
    """
    from app.models import AssetRow

    rows = (
        session.query(AssetRow)
        .order_by(AssetRow.position, AssetRow.created_at)
        .all()
    )
    assets = []
    for r in rows:
        segments = tuple(
            Segment(uri=s.uri, duration=s.duration) for s in r.segments
        )
        if segments:
            assets.append(Asset(id=r.id, title=r.title, segments=segments))
    if not assets:
        raise CatalogNotFound("no assets ingested yet — POST a file to /ingest")
    return assets


def load_schedule_from_db(session, catalog: dict[str, Asset]):
    """Build a ScheduleDef from DB rows, or return None if none is defined.

    Returns None (channel falls back to looping the catalog) when there are no
    entries, no filler is set, or a referenced asset is missing — the schedule
    endpoint's validator prevents the last case for schedules created via the
    API, but a later catalog deletion could leave a dangling reference.
    """
    from app.core.schedule import ScheduleDef
    from app.models import AdBreakRow, ChannelSettingsRow, ScheduleEntryRow

    settings = session.get(ChannelSettingsRow, "demo")
    if settings is None or not settings.filler_asset_id:
        return None
    filler = catalog.get(settings.filler_asset_id)
    if filler is None:
        return None

    rows = session.query(ScheduleEntryRow).order_by(ScheduleEntryRow.start_offset).all()
    entries = []
    for r in rows:
        asset = catalog.get(r.asset_id)
        if asset is None:
            return None
        entries.append((asset, r.start_offset))
    if not entries:
        return None

    ad_breaks = [
        (r.start_offset, r.duration)
        for r in session.query(AdBreakRow).order_by(AdBreakRow.start_offset).all()
    ]
    return ScheduleDef.build(entries, filler, settings.period_seconds, ad_breaks)
