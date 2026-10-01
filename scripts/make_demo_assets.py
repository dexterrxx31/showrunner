#!/usr/bin/env python3
"""Generate synthetic demo assets and the Phase-0 catalog.

Creates three visually distinct test videos with FFmpeg's lavfi sources
(no copyrighted media), normalizes them to one uniform spec, pre-cuts 4s
HLS segments, ffprobes every segment for its EXACT duration, and writes
data/catalog.json. This mirrors what the Phase-2 ingest pipeline will do
to real uploads.

Usage: python scripts/make_demo_assets.py [--fast]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEGMENT_DIR = ROOT / "data" / "segments"
CATALOG = ROOT / "data" / "catalog.json"

ASSETS = [
    {"id": "feature_a", "title": "Feature A (test pattern)", "src": "testsrc2", "tone": 440, "duration": 60},
    {"id": "feature_b", "title": "Feature B (SMPTE bars)", "src": "smptebars", "tone": 554, "duration": 40},
    {"id": "ident", "title": "Channel ident (classic bars)", "src": "testsrc", "tone": 659, "duration": 20},
]


def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"command failed: {' '.join(cmd)}\n{result.stderr[-2000:]}")
    return result.stdout


def probe_duration(path: Path) -> float:
    out = run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0", str(path),
        ]
    )
    return float(out.strip())


def make_asset(spec: dict, fast: bool) -> dict:
    out_dir = SEGMENT_DIR / spec["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.ts"):
        old.unlink()
    duration = max(8, spec["duration"] // 2) if fast else spec["duration"]
    print(f"  {spec['id']}: {duration}s of {spec['src']} ...")
    run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"{spec['src']}=size=1280x720:rate=25",
            "-f", "lavfi", "-i", f"sine=frequency={spec['tone']}:sample_rate=48000",
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-force_key_frames", "expr:gte(t,n_forced*4)",
            "-x264-params", "scenecut=0",
            "-c:a", "aac", "-b:a", "128k",
            "-f", "segment", "-segment_time", "4",
            "-segment_format", "mpegts",
            str(out_dir / "%05d.ts"),
        ]
    )
    segments = []
    for ts in sorted(out_dir.glob("*.ts")):
        segments.append(
            {
                "uri": f"/segments/{spec['id']}/{ts.name}",
                "duration": round(probe_duration(ts), 3),
            }
        )
    return {"id": spec["id"], "title": spec["title"], "segments": segments}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", action="store_true", help="shorter assets")
    args = parser.parse_args()

    print("generating demo assets:")
    catalog = {"assets": [make_asset(a, args.fast) for a in ASSETS]}
    CATALOG.write_text(json.dumps(catalog, indent=2))
    total = sum(s["duration"] for a in catalog["assets"] for s in a["segments"])
    n = sum(len(a["segments"]) for a in catalog["assets"])
    print(f"wrote {CATALOG}: {len(catalog['assets'])} assets, "
          f"{n} segments, {total:.1f}s cycle")


if __name__ == "__main__":
    main()
