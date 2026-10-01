"""Snapshot fidelity: rendering from a snapshot must match the canonical output.

This is the Python side of the cross-language contract. If `render_from_snapshot`
matches `render_media_playlist(timeline.window(...))` everywhere, and the Go
origin matches `render_from_snapshot` (via the golden fixtures), then Go matches
Python transitively.
"""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.manifest import render_media_playlist
from app.core.schedule import ScheduleDef, ScheduledTimeline
from app.core.snapshot import export_snapshot, render_from_snapshot
from app.core.timeline import Asset, LoopingTimeline, Segment

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parent.parent


def asset(aid, n, d=4.0):
    return Asset(
        id=aid,
        title=aid.title(),
        segments=tuple(
            Segment(uri=f"/segments/{aid}/{i:05d}.ts", duration=d) for i in range(n)
        ),
    )


def _sweep_matches(tl, span, size=6):
    snap = export_snapshot(tl)
    g = 0
    while tl._start_offset(g) < span:
        off = tl._start_offset(g)
        for delta in (0.001, tl._start_offset(g + 1) - off - 0.001 if g + 1 else 0.5):
            now = EPOCH + timedelta(seconds=off + max(0.0, delta) / 2)
            assert render_from_snapshot(snap, now, size) == render_media_playlist(
                tl.window(now, size)
            )
        g += 1


def test_snapshot_matches_scheduled_timeline():
    tl = ScheduledTimeline(
        ScheduleDef.build(
            [(asset("a", 3), 0.0), (asset("b", 5), 40.0)],
            asset("f", 1),
            period=120.0,
            ad_breaks=[(36.0, 12.0)],
        ),
        epoch=EPOCH,
    )
    _sweep_matches(tl, span=2 * tl.cycle_duration + 5)


def test_snapshot_matches_looping_timeline():
    tl = LoopingTimeline([asset("a", 3), asset("b", 2), asset("c", 4)], epoch=EPOCH)
    _sweep_matches(tl, span=2 * tl.cycle_duration + 5)


def test_snapshot_export_shape():
    tl = LoopingTimeline([asset("a", 2)], epoch=EPOCH)
    snap = export_snapshot(tl)
    assert snap["epoch"] == EPOCH.isoformat()
    assert len(snap["segments"]) == 2
    assert snap["segments"][0]["internal_disc"] == 0  # index 0 never a discontinuity
    assert set(snap["segments"][0]) >= {"uri", "duration", "start_offset", "cue_out"}


def test_go_fixtures_are_up_to_date():
    # The committed Go fixtures must equal a fresh regeneration; otherwise the
    # Go origin is being validated against a stale copy of the Python renderer.
    committed = {
        name: json.loads((ROOT / "go-origin" / "testdata" / name).read_text())
        for name in ("snapshot.json", "golden.json")
    }
    subprocess.run(
        [sys.executable, "scripts/gen_go_fixtures.py"], cwd=ROOT, check=True
    )
    for name, old in committed.items():
        fresh = json.loads((ROOT / "go-origin" / "testdata" / name).read_text())
        assert fresh == old, f"{name} is stale; commit the regenerated fixtures"
