"""Compile a timeline into a portable cycle snapshot.

This is the control-plane → data-plane handoff. Python owns scheduling (the
hard, infrequent work: resolving programmes, filler, discontinuities); it emits
a flat description of one cycle that any stateless origin, the Go service in
`go-origin/`, can serve manifests from with pure arithmetic. The snapshot
carries precomputed discontinuity flags so the origin never re-derives them.
"""

from __future__ import annotations

import bisect
from datetime import datetime, timedelta

from app.core.manifest import render_media_playlist
from app.core.timeline import ChannelNotStarted, PlayoutSegment, Window

_EPS = 1e-6


def export_snapshot(timeline) -> dict:
    """Build a snapshot from a LoopingTimeline or ScheduledTimeline.

    Works through the public window() output so both timeline types export
    identically; the snapshot is exactly what the origin needs and nothing
    about the timeline's internals leaks in.
    """
    n = timeline._n
    segments = []
    for g in range(n):
        offset = timeline._start_offset(g)
        seg = timeline.window(
            timeline.epoch + timedelta(seconds=offset + _EPS), size=1
        ).segments[0]
        segments.append(
            {
                "uri": seg.uri,
                "duration": seg.duration,
                "asset_id": seg.asset_id,
                "asset_title": seg.asset_title,
                "start_offset": offset,
                # discontinuity at a non-boundary segment (global index 0 is
                # never a discontinuity; the wrap boundary is stored separately)
                "internal_disc": 1 if (g > 0 and seg.discontinuity) else 0,
                "cue_out": seg.cue_out,
                "cue_in": bool(seg.cue_in),
            }
        )
    return {
        "epoch": timeline.epoch.isoformat(),
        "cycle_duration": timeline.cycle_duration,
        "target_duration": timeline.target_duration,
        # discontinuity across the cycle boundary (last segment → first of next)
        "wrap_disc": 1 if timeline._discontinuity(n) else 0,
        "segments": segments,
    }


# --- reference origin (the algorithm the Go service implements) ----------


def _disc(snap: dict, g: int) -> bool:
    if g == 0:
        return False
    n = len(snap["segments"])
    idx = g % n
    if idx == 0:
        return bool(snap["wrap_disc"])
    return bool(snap["segments"][idx]["internal_disc"])


def _disc_before(snap: dict, g: int) -> int:
    if g <= 0:
        return 0
    segs = snap["segments"]
    n = len(segs)
    internal_total = sum(s["internal_disc"] for s in segs)
    full, rem = divmod(g, n)
    total = full * internal_total + sum(s["internal_disc"] for s in segs[1:rem])
    total += ((g - 1) // n) * snap["wrap_disc"]
    return total


def render_from_snapshot(snap: dict, now: datetime, size: int = 5) -> str:
    """Render a manifest purely from a snapshot: the reference the Go origin
    mirrors. Reuses render_media_playlist so it is byte-identical to the
    canonical output by construction; the Go port is validated against it."""
    epoch = datetime.fromisoformat(snap["epoch"])
    segs = snap["segments"]
    n = len(segs)
    cyc = snap["cycle_duration"]
    starts = [s["start_offset"] for s in segs]

    position = (now - epoch).total_seconds()
    if position < 0:
        raise ChannelNotStarted(f"channel starts at {snap['epoch']}")
    cycle, in_cycle = divmod(position, cyc)
    cycle = int(cycle)
    idx = bisect.bisect_right(starts, in_cycle) - 1
    current = cycle * n + idx
    first = max(0, current - size + 1)

    out = []
    for g in range(first, current + 1):
        s = segs[g % n]
        out.append(
            PlayoutSegment(
                uri=s["uri"],
                duration=s["duration"],
                asset_id=s["asset_id"],
                asset_title=s["asset_title"],
                discontinuity=_disc(snap, g),
                program_datetime=epoch
                + timedelta(seconds=(g // n) * cyc + starts[g % n]),
                cue_out=s["cue_out"],
                cue_in=s["cue_in"],
            )
        )
    return render_media_playlist(
        Window(
            segments=tuple(out),
            media_sequence=first,
            discontinuity_sequence=_disc_before(snap, first),
            target_duration=snap["target_duration"],
        )
    )
