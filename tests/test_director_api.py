"""The /schedule/generate endpoint and asset metadata endpoints."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.routers.channel as channel
from app import config
from app.ai import director
from app.main import app
from tests.test_director import FakeClient, response, tool_use

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


def test_generate_persists_and_airs(client, monkeypatch):
    script = [
        response("tool_use", [tool_use("t1", "list_assets", {})]),
        response(
            "tool_use",
            [
                tool_use(
                    "t2",
                    "submit_schedule",
                    {
                        "filler_asset_id": "ident",
                        "entries": [{"asset_id": "movie", "start_offset": 20}],
                        "ad_breaks": [{"start_offset": 16, "duration": 8}],
                    },
                )
            ],
        ),
    ]
    monkeypatch.setattr(director, "make_client", lambda provider: FakeClient(script))

    r = client.post("/schedule/generate", json={"brief": "movie night", "period_seconds": 60})
    assert r.status_code == 200
    assert r.json()["entries"] == [{"asset_id": "movie", "start_offset": 20.0}]

    # The generated schedule was persisted and now drives the channel.
    sched = client.get("/schedule").json()
    assert sched["entries"][0]["asset_id"] == "movie"
    assert sched["ad_breaks"] == [{"start_offset": 16.0, "duration": 8.0}]

    monkeypatch.setattr(channel, "utcnow", lambda: EPOCH + timedelta(seconds=25))
    channel.reset_timeline()
    assert client.get("/channel/demo/now").json()["on_air"]["asset_id"] == "movie"


def test_generate_503_without_credentials(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    r = client.post("/schedule/generate", json={"brief": "x"})
    assert r.status_code == 503


def test_generate_passes_provider_and_model(client, monkeypatch):
    seen = {}

    def fake_make_client(provider):
        seen["provider"] = provider
        return FakeClient([])

    def fake_build(brief, period, session, client, *, provider, model):
        seen["model"] = model
        raise director.DirectorError("stop here")

    monkeypatch.setattr(director, "make_client", fake_make_client)
    monkeypatch.setattr(director, "build_schedule", fake_build)
    r = client.post(
        "/schedule/generate",
        json={"brief": "x", "provider": "google", "model": "gemini-2.5-flash"},
    )
    assert r.status_code == 422
    assert seen == {"provider": "google", "model": "gemini-2.5-flash"}


def test_generate_rejects_unknown_provider(client):
    r = client.post("/schedule/generate", json={"brief": "x", "provider": "nope"})
    assert r.status_code == 422


def test_list_and_patch_asset_metadata(client):
    assert {a["asset_id"] for a in client.get("/assets").json()} == {"movie", "ident"}

    r = client.patch(
        "/assets/movie", json={"genre": "action", "rating": "PG-13", "year": 1994}
    )
    assert r.status_code == 200
    assert r.json()["genre"] == "action"

    movie = next(a for a in client.get("/assets").json() if a["asset_id"] == "movie")
    assert (movie["genre"], movie["rating"], movie["year"]) == ("action", "PG-13", 1994)


def test_patch_unknown_asset_404(client):
    assert client.patch("/assets/ghost", json={"genre": "x"}).status_code == 404


@pytest.mark.parametrize(
    "exc, status",
    [
        (director.DirectorUpstreamError("anthropic provider request failed: HTTP 400: x"), 502),
        (director.DirectorUnavailable("no credentials"), 503),
    ],
)
def test_generate_maps_provider_failures(client, monkeypatch, exc, status):
    def fake_build(brief, period, session, client, *, provider, model):
        raise exc

    monkeypatch.setattr(director, "make_client", lambda provider: FakeClient([]))
    monkeypatch.setattr(director, "build_schedule", fake_build)
    r = client.post("/schedule/generate", json={"brief": "x"})
    assert r.status_code == status
    assert str(exc) in r.json()["detail"]


def test_generate_accepts_groq_provider(client, monkeypatch):
    seen = {}

    def fake_make_client(provider):
        seen["provider"] = provider
        raise director.DirectorUnavailable("stop here")

    monkeypatch.setattr(director, "make_client", fake_make_client)
    r = client.post("/schedule/generate", json={"brief": "x", "provider": "groq"})
    assert r.status_code == 503
    assert seen == {"provider": "groq"}
