"""Ad-break cue markers in the manifest, and their validation."""

from datetime import datetime, timedelta, timezone

from app.core.manifest import render_media_playlist
from app.core.schedule import ScheduleDef, ScheduledTimeline, validate_schedule
from app.core.timeline import Asset, Segment

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def asset(aid, n, d=4.0):
    return Asset(
        id=aid,
        title=aid.upper(),
        segments=tuple(
            Segment(uri=f"/segments/{aid}/{i:05d}.ts", duration=d) for i in range(n)
        ),
    )


def test_cue_markers_render_around_the_avail():
    # movie 0-12s, then filler; ad avail 20-30s.
    tl = ScheduledTimeline(
        ScheduleDef.build(
            [(asset("movie", 3), 0.0)], asset("id", 1), 60.0, ad_breaks=[(20.0, 10.0)]
        ),
        epoch=EPOCH,
    )
    w = tl.window(EPOCH + timedelta(seconds=33), size=6)
    m = render_media_playlist(w)
    assert "#EXT-X-CUE-OUT:10.000" in m
    assert "#EXT-X-CUE-IN" in m
    assert m.index("#EXT-X-CUE-OUT") < m.index("#EXT-X-CUE-IN")


def test_no_cue_markers_without_ad_breaks():
    tl = ScheduledTimeline(
        ScheduleDef.build([(asset("movie", 3), 0.0)], asset("id", 1), 60.0),
        epoch=EPOCH,
    )
    m = render_media_playlist(tl.window(EPOCH + timedelta(seconds=33), size=6))
    assert "CUE-OUT" not in m and "CUE-IN" not in m


def test_cue_avail_recurs_each_cycle():
    tl = ScheduledTimeline(
        ScheduleDef.build(
            [(asset("movie", 3), 0.0)], asset("id", 1), 60.0, ad_breaks=[(20.0, 10.0)]
        ),
        epoch=EPOCH,
    )
    # Same avail offset in the second cycle.
    m = render_media_playlist(
        tl.window(EPOCH + timedelta(seconds=tl.cycle_duration + 33), size=6)
    )
    assert "#EXT-X-CUE-OUT:10.000" in m


def test_validate_rejects_ad_break_outside_period():
    errs = validate_schedule(
        [(asset("a", 3), 0.0)], asset("f", 1), 30.0, ad_breaks=[(28.0, 10.0)]
    )
    assert any("ad break" in e for e in errs)


def test_validate_accepts_valid_ad_break():
    errs = validate_schedule(
        [(asset("a", 3), 0.0)], asset("f", 1), 60.0, ad_breaks=[(20.0, 10.0)]
    )
    assert errs == []
