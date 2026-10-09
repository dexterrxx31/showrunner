"""API-key protection: writes need the key when one is configured."""

import pytest
from fastapi.testclient import TestClient

import app.routers.channel as channel
from app import config
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path/'t.db'}")
    monkeypatch.setattr(config, "CATALOG_SOURCE", "db")
    import app.db as db_module

    db_module.reset_engine()
    db_module.init_db()
    channel.reset_timeline()
    with TestClient(app) as c:
        yield c
    channel.reset_timeline()
    db_module.reset_engine()


def _create(client, **kwargs):
    return client.post("/channels", json={"id": "news", "name": "News"}, **kwargs)


def test_writes_are_open_when_no_key_configured(client, monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "")
    assert _create(client).status_code == 201


def test_write_without_key_is_rejected(client, monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "s3cret")
    r = _create(client)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_write_with_wrong_key_is_rejected(client, monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "s3cret")
    assert _create(client, headers={"X-API-Key": "nope"}).status_code == 401
    assert _create(client, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert _create(client, headers={"Authorization": "Basic s3cret"}).status_code == 401


def test_write_with_valid_key_succeeds(client, monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "s3cret")
    assert _create(client, headers={"X-API-Key": "s3cret"}).status_code == 201
    r = client.delete("/channels/news", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200


@pytest.mark.parametrize(
    "method, path",
    [
        ("put", "/schedule"),
        ("post", "/schedule/generate"),
        ("post", "/ingest"),
        ("post", "/channel/demo/encode/start"),
        ("patch", "/assets/x"),
        ("delete", "/schedule"),
    ],
)
def test_every_write_route_requires_the_key(client, monkeypatch, method, path):
    monkeypatch.setattr(config, "API_KEY", "s3cret")
    assert getattr(client, method)(path).status_code == 401


def test_reads_stay_public_with_a_key_configured(client, monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "s3cret")
    assert client.get("/health").status_code == 200
    assert client.get("/channels").status_code == 200
    assert client.get("/channel/demo/now").status_code != 401
