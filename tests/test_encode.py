"""True-encode: command building, the process supervisor, and a real encode."""

import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.core.encode import (
    Encoder,
    branding_filter,
    build_encode_cmd,
    write_concat_file,
)

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


# --- pure command building -----------------------------------------------


def test_branding_filter_without_font_is_bar_only():
    f = branding_filter("News 24", None)
    assert "drawbox" in f and "drawtext" not in f


def test_branding_filter_with_font_adds_label():
    f = branding_filter("News 24", "/fonts/x.ttf")
    assert "drawtext" in f and "News 24" in f and "fontfile=/fonts/x.ttf" in f


def test_build_encode_cmd_shape():
    cmd = build_encode_cmd(
        "/c/concat.txt", "/c/out", channel_name="TEST", fontfile="/f.ttf",
        segment_seconds=4, window=6,
    )
    assert cmd[0] == "ffmpeg"
    assert "-stream_loop" in cmd and "-1" in cmd  # loops the cycle forever
    assert "concat" in cmd and "/c/concat.txt" in cmd
    assert "drawtext" in cmd[cmd.index("-vf") + 1]
    assert "-f" in cmd and "hls" in cmd
    assert "delete_segments+omit_endlist" in cmd
    assert cmd[-1].endswith("out/playlist.m3u8")


def test_build_encode_cmd_no_loop_omits_stream_loop():
    cmd = build_encode_cmd("/c.txt", "/o", channel_name="X", loop=False)
    assert "-stream_loop" not in cmd


def test_write_concat_file_uses_absolute_paths(tmp_path):
    dest = write_concat_file(["a.ts", tmp_path / "b.ts"], tmp_path / "concat.txt")
    lines = dest.read_text().splitlines()
    assert len(lines) == 2
    assert all(line.startswith("file '/") for line in lines)  # absolute


# --- process supervisor (no ffmpeg needed) -------------------------------


def test_encoder_lifecycle():
    enc = Encoder()
    sleeper = [sys.executable, "-c", "import time; time.sleep(30)"]
    assert enc.is_running("c1") is False
    enc.start("c1", sleeper)
    assert enc.is_running("c1") is True
    assert enc.stop("c1") is True
    assert enc.is_running("c1") is False
    assert enc.stop("c1") is False  # already stopped


def test_encoder_start_replaces_previous():
    enc = Encoder()
    sleeper = [sys.executable, "-c", "import time; time.sleep(30)"]
    enc.start("c1", sleeper)
    first = enc._procs["c1"].pid
    enc.start("c1", sleeper)  # restart
    assert enc._procs["c1"].pid != first
    enc.stop_all()
    assert enc.is_running("c1") is False


# --- real encode (ffmpeg) ------------------------------------------------


@requires_ffmpeg
def test_encode_produces_live_hls(tmp_path):
    src = tmp_path / "clip.ts"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25",
            "-f", "lavfi", "-i", "sine=frequency=440",
            "-t", "4", "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", str(src),
        ],
        check=True, capture_output=True,
    )
    out = tmp_path / "out"
    out.mkdir()
    concat = write_concat_file([src, src], out / "concat.txt")
    # Bar-only branding (no font dependency) keeps the test robust.
    cmd = build_encode_cmd(
        concat, out, channel_name="TEST", fontfile=None, segment_seconds=2
    )

    enc = Encoder()
    enc.start("t", cmd)
    try:
        playlist = out / "playlist.m3u8"
        deadline = time.time() + 15
        while time.time() < deadline:
            if playlist.exists() and list(out.glob("seg_*.ts")):
                break
            time.sleep(0.3)
    finally:
        enc.stop("t")

    assert playlist.exists(), "encoder did not produce a playlist"
    assert list(out.glob("seg_*.ts")), "encoder did not produce segments"
    assert "#EXTM3U" in playlist.read_text()
