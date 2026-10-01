"""True-encode playout: one continuous FFmpeg process per channel.

An alternative to the stateless manifest-stitching origin. Instead of pointing
players at pre-cut segments, this re-encodes the channel's assets into a single
continuous HLS stream with the channel's branding burned in, the way a
traditional playout chain (Cinegy, Grass Valley) produces a channel. It trades
the stateless/restart-safe properties for frame-accurate on-screen graphics.

The command builder and concat writer are pure (unit-tested); `Encoder`
supervises the long-running FFmpeg subprocess per channel.
"""

from __future__ import annotations

import functools
import subprocess
import threading
from pathlib import Path


@functools.lru_cache(maxsize=1)
def ffmpeg_has_drawtext() -> bool:
    """Whether the local ffmpeg was built with the drawtext filter (freetype).

    Not all builds include it; if it's missing, drawtext makes ffmpeg exit with
    'No such filter', so callers fall back to bar-only branding.
    """
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "drawtext" in out.stdout


def find_default_font() -> str | None:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Debian/Ubuntu (CI)
        "/System/Library/Fonts/Supplemental/Arial.ttf",  # macOS
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",  # Arch
    ):
        if Path(path).exists():
            return path
    return None


def branding_filter(channel_name: str, fontfile: str | None) -> str:
    """A translucent lower-third bar, with the channel name if a font exists."""
    bar = "drawbox=x=0:y=ih-64:w=iw:h=64:color=black@0.55:t=fill"
    if not fontfile:
        return bar
    text = channel_name.replace("\\", "").replace(":", r"\:").replace("'", "")
    label = (
        f"drawtext=fontfile={fontfile}:text='{text}'"
        ":x=24:y=h-46:fontsize=30:fontcolor=white"
    )
    return f"{bar},{label}"


def build_encode_cmd(
    concat_file: str | Path,
    out_dir: str | Path,
    *,
    channel_name: str,
    fontfile: str | None = None,
    segment_seconds: int = 4,
    window: int = 6,
    loop: bool = True,
) -> list[str]:
    out_dir = Path(out_dir)
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if loop:
        cmd += ["-stream_loop", "-1"]  # replay the cycle forever → continuous channel
    cmd += ["-f", "concat", "-safe", "0", "-i", str(concat_file)]
    cmd += ["-vf", branding_filter(channel_name, fontfile)]
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000",
        "-f", "hls",
        "-hls_time", str(segment_seconds),
        "-hls_list_size", str(window),
        "-hls_flags", "delete_segments+omit_endlist",
        "-hls_segment_filename", str(out_dir / "seg_%05d.ts"),
        str(out_dir / "playlist.m3u8"),
    ]
    return cmd


def write_concat_file(segment_paths: list[str | Path], dest: str | Path) -> Path:
    dest = Path(dest)
    lines = [f"file '{Path(p).resolve()}'" for p in segment_paths]
    dest.write_text("\n".join(lines) + "\n")
    return dest


class Encoder:
    """Supervises one FFmpeg true-encode process per channel."""

    def __init__(self) -> None:
        self._procs: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    def start(self, channel_id: str, cmd: list[str]) -> None:
        with self._lock:
            self._stop_locked(channel_id)
            self._procs[channel_id] = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

    def is_running(self, channel_id: str) -> bool:
        with self._lock:
            proc = self._procs.get(channel_id)
            return proc is not None and proc.poll() is None

    def stop(self, channel_id: str) -> bool:
        with self._lock:
            return self._stop_locked(channel_id)

    def stop_all(self) -> None:
        with self._lock:
            for channel_id in list(self._procs):
                self._stop_locked(channel_id)

    def _stop_locked(self, channel_id: str) -> bool:
        proc = self._procs.pop(channel_id, None)
        if proc is None:
            return False
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        return True


encoder = Encoder()
