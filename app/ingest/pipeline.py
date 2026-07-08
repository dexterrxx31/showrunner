"""Ingest pipeline: normalize an uploaded file to a uniform HLS segment set.

Every asset is transcoded to ONE spec (resolution, fps, GOP, audio) with
keyframes forced at the segment boundary, so segments from different assets
are byte-for-byte interchangeable at playout time. Segment durations are
read back with ffprobe — the exact values, never nominal — because that is
what keeps the timeline resolver drift-free.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from app import config
from app.core.timeline import Asset, Segment
from app.storage import SegmentStore


class NormalizeError(RuntimeError):
    pass


def build_ffmpeg_cmd(input_path: str | Path, out_pattern: str | Path) -> list[str]:
    seg = config.SEGMENT_SECONDS
    scale = f"scale={config.NORMALIZE_WIDTH}:{config.NORMALIZE_HEIGHT}"
    return [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(input_path),
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-vf", f"{scale},fps={config.NORMALIZE_FPS}",
        "-force_key_frames", f"expr:gte(t,n_forced*{seg})",
        "-x264-params", "scenecut=0",
        "-c:a", "aac", "-ar", "48000", "-b:a", config.AUDIO_BITRATE,
        "-f", "segment", "-segment_time", str(seg),
        "-segment_format", "mpegts",
        str(out_pattern),
    ]


def probe_duration(path: str | Path) -> float:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0", str(path),
        ],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        raise NormalizeError(f"ffprobe failed for {path}: {out.stderr.strip()}")
    return round(float(out.stdout.strip()), 3)


def normalize_and_segment(
    input_path: str | Path, out_dir: str | Path
) -> list[tuple[str, float]]:
    """Transcode + segment into out_dir. Returns [(filename, duration), ...]."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.ts"):
        stale.unlink()

    result = subprocess.run(
        build_ffmpeg_cmd(input_path, out_dir / "%05d.ts"),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise NormalizeError(f"ffmpeg failed: {result.stderr.strip()[-2000:]}")

    segments = sorted(out_dir.glob("*.ts"))
    if not segments:
        raise NormalizeError("ffmpeg produced no segments")
    return [(ts.name, probe_duration(ts)) for ts in segments]


def _upsert_asset(session, asset_id: str, title: str, segments: list[Segment], position: int) -> None:
    from app.models import AssetRow, SegmentRow

    existing = session.get(AssetRow, asset_id)
    if existing is not None:
        session.delete(existing)  # cascade drops old segments
        session.flush()
    row = AssetRow(id=asset_id, title=title, position=position)
    for i, seg in enumerate(segments):
        row.segments.append(SegmentRow(idx=i, uri=seg.uri, duration=seg.duration))
    session.add(row)


def ingest_asset(
    *,
    input_path: str | Path,
    asset_id: str,
    title: str,
    store: SegmentStore,
    session,
    workdir: str | Path,
    position: int = 0,
) -> Asset:
    """Normalize a file, store its segments, and register it in the catalog."""
    work = Path(workdir) / asset_id
    seg_meta = normalize_and_segment(input_path, work)

    segments: list[Segment] = []
    for filename, duration in seg_meta:
        data = (work / filename).read_bytes()
        uri = store.put(asset_id, filename, data)
        segments.append(Segment(uri=uri, duration=duration))

    _upsert_asset(session, asset_id, title, segments, position)
    return Asset(id=asset_id, title=title, segments=tuple(segments))
