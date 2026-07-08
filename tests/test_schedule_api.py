"""Schedule CRUD over HTTP, and the channel airing a scheduled programme.

No FFmpeg needed — catalog rows are inserted directly, then the schedule is
driven through the API and the channel clock is pinned to assert what's on air.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.routers.channel as channel
from app import config
from app.main import app

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path/'t.db'}")
    monkeypatch.setattr(config, "CATALOG_SOURCE", "db")
    monkeypatch.setattr(config, "CHANNEL_EPOCH", EPOCH)
    import app.db as db_module

    db_module.reset_engine()
    db_module.init_db()

    from app.models import AssetRow, SegmentRow

    with db_module.session_scope() as s:
        for pos, (aid, n) in enumerate([("movie", 3), ("ident", 1)]):
            row = AssetRow(id=aid, title=aid.title(), position=pos)
            for i in range(n):
                row.segments.append(
                    SegmentRow(idx=i, uri=f"/segments/{aid}/{i:05d}.ts", duration=4.0)
                )
            s.add(row)

    channel.reset_timeline()
    with TestClient(app) as c:
        yield c
    channel.reset_timeline()
    db_module.reset_engine()


def _pin_clock(monkeypatch, seconds: float):
    monkeypatch.setattr(channel, "utcnow", lambda: EPOCH + timedelta(seconds=seconds))


def test_get_schedule_empty_by_default(client):
    body = client.get("/schedule").json()
    assert body["filler_asset_id"] is None
    assert body["entries"] == []


def test_put_and_get_schedule(client):
    r = client.put(
        "/schedule",
        json={
            "filler_asset_id": "ident",
            "period_seconds": 60,
            "entries": [{"asset_id": "movie", "start_offset": 20}],
        },
    )
    assert r.status_code == 200
    assert r.json()["entries"] == 1

    body = client.get("/schedule").json()
    assert body["filler_asset_id"] == "ident"
    assert body["period_seconds"] == 60
    assert body["entries"][0]["asset_id"] == "movie"
    assert body["entries"][0]["title"] == "Movie"


def test_channel_airs_filler_then_programme(client, monkeypatch):
    client.put(
        "/schedule",
        json={
            "filler_asset_id": "ident",
            "period_seconds": 60,
            "entries": [{"asset_id": "movie", "start_offset": 20}],
        },
    )
    # Leading gap 0-20s is filler; the movie airs from ~20s.
    _pin_clock(monkeypatch, 5)
    channel.reset_timeline()
    assert client.get("/channel/demo/now").json()["on_air"]["asset_id"] == "ident"

    _pin_clock(monkeypatch, 25)
    channel.reset_timeline()
    now = client.get("/channel/demo/now").json()
    assert now["on_air"]["asset_id"] == "movie"

    m = client.get("/channel/demo/playlist.m3u8")
    assert m.status_code == 200 and m.text.startswith("#EXTM3U")


def test_put_rejects_overlap(client):
    r = client.put(
        "/schedule",
        json={
            "filler_asset_id": "ident",
            "period_seconds": 60,
            "entries": [
                {"asset_id": "movie", "start_offset": 0},
                {"asset_id": "movie", "start_offset": 6},
            ],
        },
    )
    assert r.status_code == 422
    assert any("overlap" in e for e in r.json()["detail"])


def test_put_rejects_unknown_asset(client):
    r = client.put(
        "/schedule",
        json={
            "filler_asset_id": "ident",
            "period_seconds": 60,
            "entries": [{"asset_id": "ghost", "start_offset": 0}],
        },
    )
    assert r.status_code == 422
    assert "ghost" in r.json()["detail"]


def test_put_with_ad_breaks_and_epg(client, monkeypatch):
    r = client.put(
        "/schedule",
        json={
            "filler_asset_id": "ident",
            "period_seconds": 60,
            "entries": [{"asset_id": "movie", "start_offset": 0}],
            "ad_breaks": [{"start_offset": 20, "duration": 10}],
        },
    )
    assert r.status_code == 200 and r.json()["ad_breaks"] == 1
    assert client.get("/schedule").json()["ad_breaks"] == [
        {"start_offset": 20.0, "duration": 10.0}
    ]

    _pin_clock(monkeypatch, 5)
    channel.reset_timeline()
    xml = client.get("/epg.xml")
    assert xml.status_code == 200
    assert xml.headers["content-type"].startswith("application/xml")
    assert "<programme" in xml.text

    rows = client.get("/epg.json?hours=1").json()
    assert any(p["title"] == "Movie" for p in rows)


def test_put_rejects_ad_break_outside_period(client):
    r = client.put(
        "/schedule",
        json={
            "filler_asset_id": "ident",
            "period_seconds": 60,
            "entries": [{"asset_id": "movie", "start_offset": 0}],
            "ad_breaks": [{"start_offset": 55, "duration": 10}],
        },
    )
    assert r.status_code == 422
    assert any("ad break" in e for e in r.json()["detail"])


def test_clear_schedule_reverts_to_catalog_loop(client, monkeypatch):
    client.put(
        "/schedule",
        json={
            "filler_asset_id": "ident",
            "period_seconds": 60,
            "entries": [{"asset_id": "movie", "start_offset": 20}],
        },
    )
    assert client.delete("/schedule").status_code == 200
    assert client.get("/schedule").json()["entries"] == []
    # Channel still serves (now looping the whole catalog, not 503).
    _pin_clock(monkeypatch, 5)
    channel.reset_timeline()
    assert client.get("/channel/demo/now").status_code == 200
