"""24-hour soak, simulated deterministically.

Fast-forwards a full day of playout through the resolver and asserts the
properties a real soak would check: no gaps, monotonic sequences, exact
program-date-time continuity, and a live edge that always contains the wall
clock (i.e. zero drift). Because the timeline is a pure function of the clock,
this is a proof rather than a sampling: every segment in the day is visited.
"""

from datetime import datetime, timedelta, timezone

from app.core.schedule import ScheduleDef, ScheduledTimeline
from app.core.timeline import Asset, LoopingTimeline, Segment

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
DAY = 24 * 3600


def asset(aid, n, d=4.0):
    return Asset(
        id=aid,
        title=aid.upper(),
        segments=tuple(
            Segment(uri=f"/segments/{aid}/{i:05d}.ts", duration=d) for i in range(n)
        ),
    )


def scheduled():
    # A ~1h cycle: two programmes with filler around and between them.
    return ScheduledTimeline(
        ScheduleDef.build(
            [(asset("morning", 200), 0.0), (asset("feature", 400), 2000.0)],
            asset("ident", 3),
            period=3600.0,
        ),
        epoch=EPOCH,
    )


def looping():
    return LoopingTimeline(
        [asset("a", 30), asset("b", 20), asset("c", 25)], epoch=EPOCH
    )


def _walk_a_full_day(tl):
    """Visit every segment across 24h; assert per-segment invariants."""
    g = 0
    disc_seen = 0
    while tl._start_offset(g) < DAY:
        off = tl._start_offset(g)
        now = EPOCH + timedelta(seconds=off + 0.001)
        assert tl.current_index(now) == g

        w = tl.window(now, size=6)
        assert w.media_sequence == max(0, g - 5)  # contiguous, never skips
        last = w.segments[-1]
        assert last.program_datetime == EPOCH + timedelta(seconds=off)

        # PROGRAM-DATE-TIME is continuous wall-clock, even across a
        # discontinuity (which only breaks the decode timeline, not PDT).
        for a, b in zip(w.segments, w.segments[1:]):
            gap = (b.program_datetime - a.program_datetime).total_seconds()
            assert abs(gap - a.duration) < 1e-6

        if last.discontinuity:
            disc_seen += 1
        g += 1

    # Every discontinuity we counted walking the stream must equal the
    # resolver's own accounting over the same span.
    assert disc_seen == tl._discontinuities_before(g)
    return g


def test_scheduled_channel_survives_a_day():
    segments = _walk_a_full_day(scheduled())
    assert segments > 20000  # ~21.6k segments at 4s over 24h


def test_looping_channel_survives_a_day():
    assert _walk_a_full_day(looping()) > 20000


def test_wallclock_edge_never_drifts_or_stalls():
    # Step real time across 24h; the live edge must always contain 'now' and
    # the media sequence must only ever move forward.
    tl = scheduled()
    prev_media = -1
    t = 0.0
    while t < DAY:
        now = EPOCH + timedelta(seconds=t)
        w = tl.window(now, size=6)
        last = w.segments[-1]
        edge = (last.program_datetime - EPOCH).total_seconds()
        # 'now' falls within the current (live-edge) segment; no stall, no drift.
        assert edge - 1e-6 <= t < edge + last.duration + 1e-6
        assert w.media_sequence >= prev_media
        prev_media = w.media_sequence
        t += 6.5  # a step coprime-ish to 4s segments, to catch boundaries
