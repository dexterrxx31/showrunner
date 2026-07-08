"""The DB catalog must round-trip cleanly into the timeline core."""

from datetime import datetime, timedelta, timezone

import pytest

from app import config
from app.core.catalog import CatalogNotFound, load_catalog_from_db
from app.core.timeline import LoopingTimeline

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path/'t.db'}")
    import app.db as db_module

    db_module.reset_engine()
    db_module.init_db()
    yield db_module
    db_module.reset_engine()


def _add_asset(session, asset_id, title, n_segments, position=0):
    from app.models import AssetRow, SegmentRow

    row = AssetRow(id=asset_id, title=title, position=position)
    for i in range(n_segments):
        row.segments.append(
            SegmentRow(idx=i, uri=f"/segments/{asset_id}/{i:05d}.ts", duration=4.0)
        )
    session.add(row)


def test_empty_catalog_raises(db):
    with db.session_scope() as s:
        with pytest.raises(CatalogNotFound):
            load_catalog_from_db(s)


def test_roundtrip_preserves_order_and_segments(db):
    with db.session_scope() as s:
        _add_asset(s, "b", "Second", 2, position=1)
        _add_asset(s, "a", "First", 3, position=0)
    with db.session_scope() as s:
        assets = load_catalog_from_db(s)
    assert [a.id for a in assets] == ["a", "b"]  # ordered by position
    assert [len(a.segments) for a in assets] == [3, 2]
    assert assets[0].segments[0].uri == "/segments/a/00000.ts"


def test_db_catalog_drives_the_timeline(db):
    with db.session_scope() as s:
        _add_asset(s, "a", "First", 3, position=0)
        _add_asset(s, "b", "Second", 2, position=1)
    with db.session_scope() as s:
        assets = load_catalog_from_db(s)
    tl = LoopingTimeline(assets, epoch=EPOCH)
    # 5 segments x 4s = 20s cycle; t=13s lands in asset b, first segment.
    w = tl.window(EPOCH + timedelta(seconds=13), size=5)
    assert w.segments[-1].asset_id == "b"
    assert w.segments[-1].discontinuity


def test_segmentless_asset_is_skipped(db):
    with db.session_scope() as s:
        _add_asset(s, "good", "Good", 2, position=0)
        from app.models import AssetRow

        s.add(AssetRow(id="empty", title="Empty", position=1))  # no segments
    with db.session_scope() as s:
        assets = load_catalog_from_db(s)
    assert [a.id for a in assets] == ["good"]
