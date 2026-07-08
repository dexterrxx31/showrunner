import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.routers.channel as channel
from app import config
from app.main import app

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)

CATALOG = {
    "assets": [
        {
            "id": "x",
            "title": "Asset X",
            "segments": [
                {"uri": "/segments/x/00000.ts", "duration": 4.0},
                {"uri": "/segments/x/00001.ts", "duration": 4.0},
                {"uri": "/segments/x/00002.ts", "duration": 4.0},
            ],
        },
        {
            "id": "y",
            "title": "Asset Y",
            "segments": [
                {"uri": "/segments/y/00000.ts", "duration": 4.0},
                {"uri": "/segments/y/00001.ts", "duration": 4.0},
            ],
        },
    ]
}


def _reset_channel_state():
    channel._timeline = None
    channel._manifest_cache = None


@pytest.fixture
def client(tmp_path, monkeypatch):
    cat = tmp_path / "catalog.json"
    cat.write_text(json.dumps(CATALOG))
    monkeypatch.setattr(config, "CATALOG_PATH", str(cat))
    monkeypatch.setattr(config, "CHANNEL_EPOCH", EPOCH)
    monkeypatch.setattr(config, "MANIFEST_CACHE_TTL", 1.0)
    _reset_channel_state()
    yield TestClient(app)
    _reset_channel_state()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_manifest_endpoint(client):
    r = client.get("/channel/demo/playlist.m3u8")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    assert r.headers["cache-control"] == "max-age=1"
    body = r.text
    assert body.startswith("#EXTM3U")
    assert "#EXT-X-MEDIA-SEQUENCE:" in body
    assert "#EXT-X-DISCONTINUITY-SEQUENCE:" in body


def test_now_endpoint(client):
    r = client.get("/channel/demo/now")
    assert r.status_code == 200
    d = r.json()
    assert d["on_air"]["asset_id"] in {"x", "y"}
    assert set(d["up_next"]) == {"asset_id", "title"}


def test_missing_catalog_returns_503(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CATALOG_PATH", str(tmp_path / "nope.json"))
    monkeypatch.setattr(config, "CHANNEL_EPOCH", EPOCH)
    _reset_channel_state()
    r = TestClient(app).get("/channel/demo/now")
    assert r.status_code == 503
    _reset_channel_state()


def test_cache_serves_stale_within_ttl(client, monkeypatch):
    # TTL high + wall clock jumped forward: the window WOULD change, but the
    # cache must serve the stale body — proving the cache is actually hit.
    monkeypatch.setattr(config, "MANIFEST_CACHE_TTL", 3600.0)
    channel._manifest_cache = None
    clock = {"now": EPOCH}
    monkeypatch.setattr(channel, "utcnow", lambda: clock["now"])
    b1 = client.get("/channel/demo/playlist.m3u8").text
    clock["now"] = EPOCH + timedelta(seconds=40)
    b2 = client.get("/channel/demo/playlist.m3u8").text
    assert b1 == b2


def test_cache_disabled_recomputes(client, monkeypatch):
    # TTL=0 disables the cache: the same clock jump must yield a fresh window.
    monkeypatch.setattr(config, "MANIFEST_CACHE_TTL", 0.0)
    channel._manifest_cache = None
    clock = {"now": EPOCH}
    monkeypatch.setattr(channel, "utcnow", lambda: clock["now"])
    b1 = client.get("/channel/demo/playlist.m3u8").text
    clock["now"] = EPOCH + timedelta(seconds=40)
    b2 = client.get("/channel/demo/playlist.m3u8").text
    assert b1 != b2
