"""Multiple independent channels programming one shared catalog."""

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
        for pos, (aid, n) in enumerate([("movie", 3), ("show", 5), ("ident", 1)]):
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


def _sched(entries):
    return {"filler_asset_id": "ident", "period_seconds": 60, "entries": entries}


def test_two_channels_air_independently(client, monkeypatch):
    # demo shows "show" at t=0; a new "news" channel shows "movie" at t=0.
    client.put("/schedule", json=_sched([{"asset_id": "show", "start_offset": 0}]))
    assert client.post("/channels", json={"id": "news", "name": "News 24"}).status_code == 201
    client.put(
        "/channels/news/schedule",
        json=_sched([{"asset_id": "movie", "start_offset": 0}]),
    )

    monkeypatch.setattr(channel, "utcnow", lambda: EPOCH + timedelta(seconds=5))
    channel.reset_timeline()

    assert client.get("/channel/demo/now").json()["on_air"]["asset_id"] == "show"
    assert client.get("/channel/news/now").json()["on_air"]["asset_id"] == "movie"

    # Each serves its own manifest and EPG.
    assert client.get("/channel/news/playlist.m3u8").text.startswith("#EXTM3U")
    news_epg = client.get("/channel/news/epg.json").json()
    assert any(p["title"] == "Movie" for p in news_epg)
    assert not any(p["title"] == "Show" for p in news_epg)


def test_list_channels(client):
    client.put("/schedule", json=_sched([{"asset_id": "show", "start_offset": 0}]))
    client.post("/channels", json={"id": "news", "name": "News 24"})
    listed = {c["id"]: c for c in client.get("/channels").json()}
    assert listed["demo"]["has_schedule"] is True
    assert listed["news"]["name"] == "News 24"
    assert listed["news"]["has_schedule"] is False


def test_channel_epoch_is_per_channel(client, monkeypatch):
    client.post(
        "/channels", json={"id": "late", "epoch": "2026-01-01T00:00:40+00:00"}
    )
    client.put(
        "/channels/late/schedule",
        json=_sched([{"asset_id": "show", "start_offset": 0}]),
    )
    # At wall clock EPOCH+45s, "late" (epoch +40s) is only 5s into its cycle.
    monkeypatch.setattr(channel, "utcnow", lambda: EPOCH + timedelta(seconds=45))
    channel.reset_timeline()
    now = client.get("/channel/late/now").json()
    assert now["on_air"]["asset_id"] == "show"
    assert now["on_air"]["position_s"] == pytest.approx(5.0, abs=0.5)


def test_duplicate_channel_409(client):
    assert client.post("/channels", json={"id": "news"}).status_code == 201
    assert client.post("/channels", json={"id": "news"}).status_code == 409


def test_delete_channel_reverts_to_catalog_loop(client, monkeypatch):
    client.post("/channels", json={"id": "news"})
    client.put(
        "/channels/news/schedule",
        json=_sched([{"asset_id": "movie", "start_offset": 0}]),
    )
    assert client.delete("/channels/news").status_code == 200
    assert "news" not in {c["id"] for c in client.get("/channels").json()}

    # Channel id still resolves (loops the shared catalog) — not a 404/503.
    monkeypatch.setattr(channel, "utcnow", lambda: EPOCH + timedelta(seconds=5))
    channel.reset_timeline()
    assert client.get("/channel/news/now").status_code == 200
