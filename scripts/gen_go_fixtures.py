#!/usr/bin/env python3
"""Generate the cross-language test fixtures for the Go origin.

Builds a representative schedule (programmes + filler + an ad avail),
exports its snapshot, and records the canonical Python manifest at a spread of
wall-clock offsets (cycle boundaries, asset joins, the ad avail, later cycles).
The Go origin test replays these and asserts byte-identical output.

Run from the repo root: python scripts/gen_go_fixtures.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.manifest import render_media_playlist  # noqa: E402
from app.core.schedule import ScheduleDef, ScheduledTimeline  # noqa: E402
from app.core.snapshot import export_snapshot  # noqa: E402
from app.core.timeline import Asset, Segment  # noqa: E402

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
WINDOW = 6
OUT = Path(__file__).resolve().parent.parent / "go-origin" / "testdata"


def asset(aid, n, d=4.0, title=None):
    return Asset(
        id=aid,
        title=title or aid.title(),
        segments=tuple(
            Segment(uri=f"/segments/{aid}/{i:05d}.ts", duration=d) for i in range(n)
        ),
    )


def build_timeline() -> ScheduledTimeline:
    # Two programmes with uneven real-world durations, filler around/between,
    # and an ad avail — exercises joins, filler loops, cues, and cycle wrap.
    morning = Asset(
        id="morning",
        title="Morning Show",
        segments=tuple(
            Segment(uri=f"/segments/morning/{i:05d}.ts", duration=d)
            for i, d in enumerate([4.0, 4.0, 3.84])
        ),
    )
    feature = asset("feature", 5, title="Feature Film")
    ident = asset("ident", 1, title="Channel Ident")
    return ScheduledTimeline(
        ScheduleDef.build(
            [(morning, 0.0), (feature, 40.0)],
            ident,
            period=120.0,
            ad_breaks=[(36.0, 12.0)],
        ),
        epoch=EPOCH,
    )


def main() -> None:
    tl = build_timeline()
    snapshot = export_snapshot(tl)

    offsets = []
    # Every segment boundary in the first cycle + midpoints.
    g = 0
    while tl._start_offset(g) < 2 * tl.cycle_duration:
        off = tl._start_offset(g)
        offsets.append(off + 0.001)
        offsets.append(off + tl._cycle[g % tl._n].duration / 2)
        g += 1
    # A few deep-uptime samples to exercise large cycle counts.
    offsets += [1000 * tl.cycle_duration + 5, 5000 * tl.cycle_duration + 61.5]

    cases = []
    for off in offsets:
        # Render at a millisecond-rounded instant so the Go origin, which
        # reconstructs the time from offset_ms, hits the identical moment.
        off_ms = round(off * 1000)
        now = EPOCH + timedelta(milliseconds=off_ms)
        manifest = render_media_playlist(tl.window(now, size=WINDOW))
        cases.append({"offset_ms": off_ms, "manifest": manifest})

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "snapshot.json").write_text(json.dumps(snapshot, indent=2) + "\n")
    (OUT / "golden.json").write_text(
        json.dumps({"window": WINDOW, "cases": cases}, indent=2) + "\n"
    )
    print(f"wrote {OUT/'snapshot.json'} and {OUT/'golden.json'} ({len(cases)} cases)")


if __name__ == "__main__":
    main()
