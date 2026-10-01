"""True-encode endpoints: start/stop/status and their guards."""

import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.routers.channel as channel
from app import config
from app.core.encode import encoder
from app.main import app

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path/'t.db'}")
    monkeypatch.setattr(config, "CATALOG_SOURCE", "db")
    monkeypatch.setattr(config, "SEGMENT_DIR", str(tmp_path / "segments"))
    monkeypatch.setattr(config, "ENCODE_DIR", str(tmp_path / "encoded"))
    monkeypatch.setattr(config, "SEGMENT_SECONDS", 2)
    import app.db as db_module

    db_module.reset_engine()
    db_module.init_db()
    channel.reset_timeline()
    with TestClient(app) as c:
        yield c
    encoder.stop_all()
    channel.reset_timeline()
    db_module.reset_engine()


def _seed(n_segments=1, make_files=False, tmp_path: Path | None = None):
    import app.db as db_module
    from app.models import AssetRow, SegmentRow

    with db_module.session_scope() as s:
        row = AssetRow(id="clip", title="Clip", position=0)
        for i in range(n_segments):
            row.segments.append(
                SegmentRow(idx=i, uri=f"/segments/clip/{i:05d}.ts", duration=2.0)
            )
        s.add(row)


def test_status_when_not_running(client):
    r = client.get("/channel/demo/encode/status").json()
    assert r["running"] is False and r["playlist_url"] is None


def test_stop_when_not_running(client):
    assert client.post("/channel/demo/encode/stop").json()["status"] == "not_running"


def test_start_503_without_catalog(client):
    # No assets ingested at all → channel can't be built.
    assert client.post("/channel/demo/encode/start").status_code == 503


def test_start_409_when_segment_files_missing(client):
    _seed(n_segments=1, make_files=False)
    r = client.post("/channel/demo/encode/start")
    assert r.status_code == 409
    assert "not found" in r.json()["detail"]


@requires_ffmpeg
def test_start_produces_stream_then_stop(client, tmp_path):
    seg_dir = Path(config.SEGMENT_DIR) / "clip"
    seg_dir.mkdir(parents=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25",
            "-f", "lavfi", "-i", "sine=frequency=440",
            "-t", "2", "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", str(seg_dir / "00000.ts"),
        ],
        check=True, capture_output=True,
    )
    _seed(n_segments=1, make_files=True)

    r = client.post("/channel/demo/encode/start")
    assert r.status_code == 200
    assert r.json()["playlist_url"] == "/encoded/demo/playlist.m3u8"

    playlist = Path(config.ENCODE_DIR) / "demo" / "playlist.m3u8"
    deadline = time.time() + 15
    while time.time() < deadline and not playlist.exists():
        time.sleep(0.3)

    assert client.get("/channel/demo/encode/status").json()["running"] is True
    assert playlist.exists(), "no encoded playlist produced"

    assert client.post("/channel/demo/encode/stop").json()["status"] == "stopped"
    assert client.get("/channel/demo/encode/status").json()["running"] is False


@pytest.mark.parametrize("bad_id", ["..", ".", ".hidden", "UPPER", "a b", "x" * 65])
def test_encode_rejects_unsafe_channel_ids(client, bad_id):
    for method, suffix in (("post", "start"), ("post", "stop"), ("get", "status")):
        r = getattr(client, method)(f"/channel/{bad_id}/encode/{suffix}")
        assert r.status_code in (404, 422), (bad_id, suffix, r.status_code)


def test_create_channel_rejects_unsafe_id(client):
    r = client.post("/channels", json={"id": "../escape", "name": "x"})
    assert r.status_code == 422
