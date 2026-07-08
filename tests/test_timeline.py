from datetime import datetime, timedelta, timezone

import pytest

from app.core.timeline import (
    Asset,
    ChannelNotStarted,
    LoopingTimeline,
    Segment,
)

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def seg(asset: str, i: int, d: float = 4.0) -> Segment:
    return Segment(uri=f"/segments/{asset}/{i:05d}.ts", duration=d)


@pytest.fixture
def timeline() -> LoopingTimeline:
    # Asset X: 3 segments (12s), asset Y: 2 segments (8s). Cycle = 20s.
    x = Asset(id="x", title="Asset X", segments=tuple(seg("x", i) for i in range(3)))
    y = Asset(id="y", title="Asset Y", segments=tuple(seg("y", i) for i in range(2)))
    return LoopingTimeline([x, y], epoch=EPOCH)


def at(seconds: float) -> datetime:
    return EPOCH + timedelta(seconds=seconds)


def test_channel_start(timeline):
    w = timeline.window(at(0), size=5)
    assert w.media_sequence == 0
    assert w.discontinuity_sequence == 0
    assert len(w.segments) == 1
    assert not w.segments[0].discontinuity
    assert w.segments[0].uri == "/segments/x/00000.ts"


def test_before_epoch_raises(timeline):
    with pytest.raises(ChannelNotStarted):
        timeline.window(at(-1))


def test_join_carries_discontinuity(timeline):
    # t=13s: inside Y's first segment (starts at 12s).
    w = timeline.window(at(13), size=5)
    assert w.media_sequence == 0
    assert [s.asset_id for s in w.segments] == ["x", "x", "x", "y"]
    assert [s.discontinuity for s in w.segments] == [False, False, False, True]
    assert w.discontinuity_sequence == 0


def test_cycle_wrap_is_a_join(timeline):
    # t=21s: second cycle, first segment of X (global index 5).
    w = timeline.window(at(21), size=5)
    assert w.media_sequence == 1
    last = w.segments[-1]
    assert last.asset_id == "x"
    assert last.discontinuity  # wrap from Y back to X is a join
    assert last.program_datetime == at(20)


def test_discontinuity_sequence_counts_expired_joins(timeline):
    # t=33s: global index 8 (cycle 1, Y seg 0). Window starts at 4.
    w = timeline.window(at(33), size=5)
    assert w.media_sequence == 4
    # Joins before global index 4: only the X->Y join at index 3.
    assert w.discontinuity_sequence == 1


def test_media_sequence_is_monotonic(timeline):
    sequences = [
        timeline.window(at(t), size=5).media_sequence for t in range(0, 200, 3)
    ]
    assert sequences == sorted(sequences)


def test_program_datetime_never_drifts(timeline):
    # After 1000 cycles the PDT of a cycle-start segment must be exact.
    t = 1000 * timeline.cycle_duration + 1
    w = timeline.window(at(t), size=1)
    assert w.segments[0].program_datetime == at(1000 * timeline.cycle_duration)


def test_now_playing(timeline):
    info = timeline.now_playing(at(13))
    assert info.asset_id == "y"
    assert info.up_next_id == "x"
    assert info.asset_position == pytest.approx(1.0)


def test_uneven_segment_durations():
    # Real ffprobe'd durations are never exactly nominal.
    x = Asset(
        id="x",
        title="X",
        segments=(
            Segment(uri="a.ts", duration=4.096),
            Segment(uri="b.ts", duration=3.904),
        ),
    )
    tl = LoopingTimeline([x], epoch=EPOCH)
    assert tl.cycle_duration == pytest.approx(8.0)
    # 4.05s is inside the first segment (0..4.096).
    assert tl.window(at(4.05), size=1).segments[0].uri == "a.ts"
    # 4.15s is inside the second.
    assert tl.window(at(4.15), size=1).segments[0].uri == "b.ts"
    assert tl.target_duration == 5


# --- Phase 1 hardening ---------------------------------------------------


def test_single_asset_channel_loop_is_discontinuity():
    # A channel that loops one asset: every restart resets timestamps, so the
    # loop point is a genuine decode discontinuity.
    a = Asset(id="a", title="A", segments=tuple(seg("a", i) for i in range(2)))
    tl = LoopingTimeline([a], epoch=EPOCH)
    first_loop = tl.window(at(9), size=1)  # global index 2 — first restart
    assert first_loop.segments[0].discontinuity
    assert first_loop.media_sequence == 2
    assert first_loop.discontinuity_sequence == 0  # tag is here, not before it
    second_loop = tl.window(at(17), size=1)  # global index 4
    assert second_loop.discontinuity_sequence == 1


def test_window_larger_than_catalog(timeline):
    now = at(3 * timeline.cycle_duration + 1)
    current = timeline.current_index(now)
    w = timeline.window(now, size=100)
    assert len(w.segments) == min(100, current + 1)
    assert w.media_sequence == max(0, current - 99)


def test_first_window_segment_can_carry_discontinuity(timeline):
    # size=1 landing on a join: the single (first) segment must carry the tag,
    # and it must NOT be double-counted in the discontinuity sequence.
    w = timeline.window(at(13), size=1)  # Y's first segment, global index 3
    assert w.media_sequence == 3
    assert w.segments[0].asset_id == "y"
    assert w.segments[0].discontinuity
    assert w.discontinuity_sequence == 0


def test_long_uptime_resolves_exactly(timeline):
    # ~1 year on air: float seconds must still land on the right segment.
    one_year = 365 * 24 * 3600
    cycles = one_year // int(timeline.cycle_duration)
    w = timeline.window(at(cycles * timeline.cycle_duration + 1), size=1)
    assert w.segments[0].asset_id == "x"
    assert w.segments[0].uri == "/segments/x/00000.ts"


def test_discontinuity_accounting_invariant(timeline):
    # The core HLS invariant: for any window, (discontinuity_sequence + the
    # tags inside the window) equals the total joins up to the current segment.
    # And media_sequence never goes backwards as the clock advances.
    prev_media = -1
    for t in range(0, 300):
        now = at(t + 0.5)
        w = timeline.window(now, size=5)
        current = timeline.current_index(now)
        in_window = sum(s.discontinuity for s in w.segments)
        total = w.discontinuity_sequence + in_window
        assert total == timeline._discontinuities_before(current + 1)
        assert w.media_sequence >= prev_media
        prev_media = w.media_sequence


def test_consecutive_windows_never_gap(timeline):
    # Windows one target-duration apart must overlap or abut — never skip a
    # segment, which would show as a stall in the player.
    step = timeline.target_duration
    for t in range(0, 200, step):
        w1 = timeline.window(at(t), size=5)
        w2 = timeline.window(at(t + step), size=5)
        end1 = w1.media_sequence + len(w1.segments)
        assert w1.media_sequence <= w2.media_sequence <= end1
