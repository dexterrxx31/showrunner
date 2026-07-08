"""The ingest HTTP flow: upload → job completes → channel airs the asset.

FastAPI background tasks run synchronously under TestClient, so the eager
path completes before the POST returns — no broker needed.
"""

import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

import app.routers.channel as channel
from app import config
from app.main import app

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path/'t.db'}")
    monkeypatch.setattr(config, "SEGMENT_DIR", str(tmp_path / "segments"))
    monkeypatch.setattr(config, "WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setattr(config, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(config, "CATALOG_SOURCE", "db")
    monkeypatch.setattr(config, "CELERY_BROKER_URL", "")  # eager
    import app.db as db_module

    db_module.reset_engine()
    channel.reset_timeline()
    with TestClient(app) as c:  # triggers startup → init_db()
        yield c
    db_module.reset_engine()
    channel.reset_timeline()


def _clip(path, seconds=10):
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", str(seconds),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(path),
        ],
        check=True, capture_output=True,
    )


def test_channel_503_before_any_ingest(client):
    assert client.get("/channel/demo/now").status_code == 503


def test_ingest_status_404_for_unknown_job(client):
    assert client.get("/ingest/nope").status_code == 404


@requires_ffmpeg
def test_upload_then_channel_airs_it(client, tmp_path):
    src = tmp_path / "source.mp4"
    _clip(src, seconds=10)

    with src.open("rb") as f:
        r = client.post(
            "/ingest",
            data={"title": "Late Night Movie"},
            files={"file": ("source.mp4", f, "video/mp4")},
        )
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    asset_id = r.json()["asset_id"]
    assert asset_id.startswith("late-night-movie-")

    # Eager background task already ran → job is done with segments.
    status = client.get(f"/ingest/{job_id}").json()
    assert status["status"] == "done"
    assert status["segment_count"] >= 2

    # The channel now serves a manifest built from the ingested asset.
    m = client.get("/channel/demo/playlist.m3u8")
    assert m.status_code == 200
    assert m.text.startswith("#EXTM3U")
    now = client.get("/channel/demo/now").json()
    assert now["on_air"]["asset_id"] == asset_id
