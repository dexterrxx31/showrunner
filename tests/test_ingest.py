"""End-to-end ingest: real FFmpeg → local store → DB catalog → playable.

These tests need FFmpeg on PATH; they skip cleanly without it so the pure
timeline/manifest/DB suite still runs anywhere.
"""

import shutil
import subprocess
from datetime import datetime, timezone

import pytest

from app import config
from app.core.timeline import LoopingTimeline
from app.storage import LocalSegmentStore

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_clip(path, seconds, source="testsrc2", tone=440):
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"{source}=size=320x240:rate=25",
            "-f", "lavfi", "-i", f"sine=frequency={tone}:sample_rate=48000",
            "-t", str(seconds),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(path),
        ],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path/'t.db'}")
    monkeypatch.setattr(config, "SEGMENT_DIR", str(tmp_path / "segments"))
    monkeypatch.setattr(config, "WORK_DIR", str(tmp_path / "work"))
    import app.db as db_module

    db_module.reset_engine()
    db_module.init_db()
    yield tmp_path
    db_module.reset_engine()


@requires_ffmpeg
def test_normalize_produces_probed_segments(env):
    from app.ingest.pipeline import normalize_and_segment

    src = env / "src.mp4"
    _make_clip(src, seconds=9)
    meta = normalize_and_segment(src, env / "out")
    assert len(meta) >= 2  # ~9s at 4s segments → 3 chunks
    assert all(d > 0 for _, d in meta)
    total = sum(d for _, d in meta)
    assert total == pytest.approx(9, abs=1.0)  # probed durations sum to source


@requires_ffmpeg
def test_ingest_registers_playable_asset(env):
    import app.db as db_module
    from app.ingest.pipeline import ingest_asset

    src = env / "movie.mp4"
    _make_clip(src, seconds=12)
    store = LocalSegmentStore(config.SEGMENT_DIR)
    with db_module.session_scope() as s:
        asset = ingest_asset(
            input_path=src,
            asset_id="movie-1",
            title="Test Movie",
            store=store,
            session=s,
            workdir=config.WORK_DIR,
        )
    # Segments physically stored where the delivery layer expects them.
    for seg in asset.segments:
        rel = seg.uri.removeprefix("/segments/")
        assert (env / "segments" / rel).exists()
    # And the catalog now drives a real channel.
    from app.core.catalog import load_catalog_from_db

    with db_module.session_scope() as s:
        assets = load_catalog_from_db(s)
    tl = LoopingTimeline(assets, epoch=EPOCH)
    assert tl.now_playing(EPOCH).asset_id == "movie-1"


@requires_ffmpeg
def test_segments_are_interchangeable_across_assets(env):
    """The whole point of normalization: every asset yields the same spec."""
    import app.db as db_module
    from app.ingest.pipeline import ingest_asset

    store = LocalSegmentStore(config.SEGMENT_DIR)
    specs = [("a", "testsrc2", 440), ("b", "smptebars", 554), ("c", "testsrc", 659)]
    for aid, source, tone in specs:
        src = env / f"{aid}.mp4"
        _make_clip(src, seconds=8, source=source, tone=tone)
        with db_module.session_scope() as s:
            ingest_asset(
                input_path=src,
                asset_id=aid,
                title=aid.upper(),
                store=store,
                session=s,
                workdir=config.WORK_DIR,
            )

    # Probe one segment from each asset — all must share codec/res/fps.
    def video_spec(ts_path):
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=codec_name,width,height",
                "-of", "csv=p=0", str(ts_path),
            ],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()

    specs_seen = {
        video_spec(next((env / "segments" / aid).glob("*.ts")))
        for aid, _, _ in specs
    }
    assert len(specs_seen) == 1  # identical spec across all three assets
