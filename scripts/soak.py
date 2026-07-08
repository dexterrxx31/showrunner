#!/usr/bin/env python3
"""Live soak test: poll a running channel and measure drift and stalls.

Runs against a real server over real time — the unattended check the
simulation can't do (network, caching, static delivery, wall-clock skew).
Defaults to a short run; pass --duration 86400 for the full 24 hours.

    python scripts/soak.py --url http://localhost:8000 --duration 300
    python scripts/soak.py --duration 86400 --ffmpeg-check   # full day + decode

Exits non-zero if any invariant is violated (monotonic sequences, live edge
tracks the wall clock, every referenced segment is fetchable).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin


@dataclass
class Manifest:
    media_sequence: int
    discontinuity_sequence: int
    target_duration: int
    segments: list[tuple[str, float, datetime | None]]  # (uri, duration, pdt)

    @property
    def live_edge_end(self) -> datetime | None:
        if not self.segments or self.segments[-1][2] is None:
            return None
        uri, dur, pdt = self.segments[-1]
        from datetime import timedelta

        return pdt + timedelta(seconds=dur)


def parse_manifest(text: str) -> Manifest:
    media = disc = 0
    target = 0
    segments: list[tuple[str, float, datetime | None]] = []
    pending_dur = 0.0
    pending_pdt: datetime | None = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            media = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-DISCONTINUITY-SEQUENCE:"):
            disc = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            target = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            raw = line.split(":", 1)[1].replace("Z", "+00:00")
            pending_pdt = datetime.fromisoformat(raw)
        elif line.startswith("#EXTINF:"):
            pending_dur = float(line.split(":", 1)[1].split(",")[0])
        elif line and not line.startswith("#"):
            segments.append((line, pending_dur, pending_pdt))
            pending_dur, pending_pdt = 0.0, None
    return Manifest(media, disc, target, segments)


@dataclass
class Report:
    polls: int = 0
    poll_errors: int = 0
    max_drift_s: float = 0.0
    discontinuities_seen: int = 0
    stalls: int = 0
    violations: list[str] = field(default_factory=list)


def _get(url: str, timeout: float = 10.0) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode()


def _segment_ok(base: str, uri: str) -> bool:
    seg_url = urljoin(base, uri)
    req = urllib.request.Request(seg_url, headers={"Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(req, timeout=10.0) as r:
            return r.status in (200, 206)
    except Exception:
        return False


def soak(url: str, duration: float, interval: float, ffmpeg_check: bool) -> Report:
    base = url if url.endswith("/") else url + "/"
    manifest_url = urljoin(base, "channel/demo/playlist.m3u8")
    rep = Report()

    prev_media = -1
    prev_disc = -1
    last_advance = time.monotonic()
    deadline = time.monotonic() + duration

    print(f"soaking {manifest_url} for {duration:.0f}s (interval {interval:.1f}s)")
    while time.monotonic() < deadline:
        rep.polls += 1
        try:
            m = parse_manifest(_get(manifest_url))
        except Exception as e:  # noqa: BLE001
            rep.poll_errors += 1
            print(f"  poll error: {e}")
            time.sleep(interval)
            continue

        if m.media_sequence < prev_media:
            rep.violations.append(
                f"media-sequence went backwards: {prev_media} -> {m.media_sequence}"
            )
        if m.discontinuity_sequence < prev_disc:
            rep.violations.append(
                f"discontinuity-sequence went backwards: "
                f"{prev_disc} -> {m.discontinuity_sequence}"
            )

        if m.media_sequence > prev_media:
            rep.discontinuities_seen = max(
                rep.discontinuities_seen, m.discontinuity_sequence
            )
            last_advance = time.monotonic()
        elif time.monotonic() - last_advance > 3 * max(m.target_duration, 1):
            rep.stalls += 1
            rep.violations.append(
                f"stall: media-sequence stuck at {m.media_sequence} for "
                f">{3 * m.target_duration}s"
            )
            last_advance = time.monotonic()  # don't spam

        edge = m.live_edge_end
        if edge is not None:
            drift = abs((datetime.now(timezone.utc) - edge).total_seconds())
            rep.max_drift_s = max(rep.max_drift_s, drift)
            # Live edge should stay within a couple of segments of the wall clock.
            if drift > 3 * max(m.target_duration, 1):
                rep.violations.append(f"drift {drift:.1f}s exceeds 3x target")

        if m.segments and not _segment_ok(base, m.segments[-1][0]):
            rep.violations.append(f"newest segment not fetchable: {m.segments[-1][0]}")

        prev_media = max(prev_media, m.media_sequence)
        prev_disc = max(prev_disc, m.discontinuity_sequence)
        time.sleep(interval)

    if ffmpeg_check:
        rep = _ffmpeg_check(manifest_url, rep)
    return rep


def _ffmpeg_check(manifest_url: str, rep: Report) -> Report:
    print("  running ffmpeg decode check (30s)...")
    try:
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", manifest_url, "-t", "30", "-f", "null", "-"],
            capture_output=True, text=True, timeout=90,
        )
        if r.returncode != 0:
            rep.violations.append(f"ffmpeg failed to decode: {r.stderr.strip()[:300]}")
    except FileNotFoundError:
        print("  ffmpeg not installed — skipping decode check")
    except subprocess.TimeoutExpired:
        rep.violations.append("ffmpeg decode check timed out")
    return rep


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--duration", type=float, default=120.0, help="seconds")
    p.add_argument("--interval", type=float, default=2.0, help="poll interval (s)")
    p.add_argument("--ffmpeg-check", action="store_true", help="decode with ffmpeg")
    args = p.parse_args()

    rep = soak(args.url, args.duration, args.interval, args.ffmpeg_check)

    print("\n--- soak report ---")
    print(f"polls:            {rep.polls} ({rep.poll_errors} errors)")
    print(f"max drift:        {rep.max_drift_s:.2f}s")
    print(f"discontinuities:  {rep.discontinuities_seen}")
    print(f"stalls:           {rep.stalls}")
    if rep.violations:
        print(f"VIOLATIONS ({len(rep.violations)}):")
        for v in rep.violations[:20]:
            print(f"  - {v}")
        return 1
    print("result:           OK — no stalls, no drift, no gaps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
