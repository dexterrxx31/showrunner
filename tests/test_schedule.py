from datetime import datetime, timedelta, timezone

import pytest

from app.core.schedule import ScheduleDef, ScheduledTimeline, validate_schedule
from app.core.timeline import Asset, ChannelNotStarted, Segment

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def asset(aid: str, n: int, d: float = 4.0, title: str | None = None) -> Asset:
    return Asset(
        id=aid,
        title=title or aid.upper(),
        segments=tuple(
            Segment(uri=f"/segments/{aid}/{i:05d}.ts", duration=d) for i in range(n)
        ),
    )


def at(seconds: float) -> datetime:
    return EPOCH + timedelta(seconds=seconds)


def make(entries, filler, period) -> ScheduledTimeline:
    return ScheduledTimeline(ScheduleDef.build(entries, filler, period), epoch=EPOCH)


# --- filler & gaps -------------------------------------------------------


def test_filler_fills_leading_gap_then_programme_airs():
    tl = make([(asset("movie", 3), 20.0)], asset("id", 1), period=40.0)
    assert tl.now_playing(at(0.5)).asset_id == "id"  # filler before the programme
    assert tl.now_playing(at(21)).asset_id == "movie"  # programme after target
    assert tl.now_playing(at(35)).asset_id == "id"  # tail filler after it ends


def test_back_to_back_entries_get_discontinuity_within_entry_none():
    tl = make([(asset("a", 3), 0.0), (asset("b", 2), 12.0)], asset("f", 1), 20.0)
    w = tl.window(at(19), size=5)
    assert [(s.asset_id, s.discontinuity) for s in w.segments] == [
        ("a", False),
        ("a", False),
        ("a", False),
        ("b", True),   # asset change → discontinuity
        ("b", False),
    ]


def test_single_segment_filler_loop_is_discontinuity_each_restart():
    # A 4s filler tiling a 12s leading gap loops 3x; each restart breaks decode.
    # Sampled in the second cycle so the window isn't anchored at global 0
    # (which is never a discontinuity, by HLS convention).
    tl = make([(asset("movie", 2), 12.0)], asset("id", 1), period=20.0)
    w = tl.window(at(tl.cycle_duration + 11), size=3)  # three looped filler segs
    assert all(s.asset_id == "id" for s in w.segments)
    assert [s.discontinuity for s in w.segments] == [True, True, True]


# --- resolver correctness (brute-force oracle) ---------------------------


def test_resolver_matches_bruteforce():
    tl = make([(asset("a", 3), 0.0), (asset("b", 2), 20.0)], asset("f", 2), 40.0)
    n = tl._n

    def cont(x, y):
        return x.asset_id == y.asset_id and y.within_idx == x.within_idx + 1

    expanded = [tl._cycle[g % n] for g in range(4 * n)]

    def exp_disc(g):
        return False if g == 0 else not cont(expanded[g - 1], expanded[g])

    for g in range(3 * n):
        assert tl._discontinuity(g) == exp_disc(g), g
        assert tl._discontinuities_before(g) == sum(exp_disc(k) for k in range(g)), g

    for g in [0, 1, n - 1, n, n + 1, 2 * n + 3]:
        now = at(tl._start_offset(g) + 0.01)
        assert tl.current_index(now) == g
        w = tl.window(now, size=5)
        first = max(0, g - 4)
        assert w.media_sequence == first
        assert w.discontinuity_sequence == sum(exp_disc(k) for k in range(first))
        assert [s.discontinuity for s in w.segments] == [
            exp_disc(k) for k in range(first, g + 1)
        ]
        assert w.segments[-1].program_datetime == at(tl._start_offset(g))


def test_program_datetime_no_drift_over_many_cycles():
    tl = make([(asset("a", 3), 0.0), (asset("b", 2), 20.0)], asset("f", 2), 40.0)
    t = 1000 * tl.cycle_duration + 1
    w = tl.window(at(t), size=1)
    assert w.segments[0].program_datetime == at(1000 * tl.cycle_duration)


def test_before_epoch_raises():
    tl = make([(asset("a", 2), 0.0)], asset("f", 1), 8.0)
    with pytest.raises(ChannelNotStarted):
        tl.window(at(-1))


def test_now_playing_up_next_wraps():
    tl = make(
        [(asset("a", 3, title="A"), 0.0), (asset("b", 2, title="B"), 12.0)],
        asset("f", 1, title="F"),
        20.0,
    )
    first = tl.now_playing(at(1))
    assert (first.asset_id, first.up_next_id) == ("a", "b")
    second = tl.now_playing(at(13))
    assert second.asset_id == "b"
    assert second.up_next_id == "a"  # wraps to the first block of the next cycle


# --- validation ----------------------------------------------------------


def test_validate_accepts_a_clean_schedule():
    errs = validate_schedule(
        [(asset("a", 3), 0.0), (asset("b", 2), 12.0)], asset("f", 1), 30.0
    )
    assert errs == []


def test_validate_rejects_overlap():
    errs = validate_schedule(
        [(asset("a", 3), 0.0), (asset("b", 3), 6.0)], asset("f", 1), 30.0
    )
    assert any("overlap" in e for e in errs)


def test_validate_rejects_exceeding_period():
    errs = validate_schedule([(asset("a", 3), 20.0)], asset("f", 1), 25.0)
    assert any("period" in e for e in errs)


def test_validate_requires_filler():
    errs = validate_schedule([(asset("a", 3), 0.0)], None, 30.0)
    assert any("filler" in e for e in errs)
