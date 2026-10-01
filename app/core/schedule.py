"""Scheduled playout: resolve a programme schedule (with filler) to a window.

A schedule places assets at target offsets within a repeating cycle; the space
around and between them is tiled with a looping filler asset so the channel is
never off air. The whole cycle is flattened once into a segment list, then
resolved against the wall clock exactly like the looping timeline, so the
same drift-free, stateless, restart-safe properties hold, and the manifest
generator consumes the identical `Window` contract.

Timing is segment-quantized: filler is laid as whole segments, so a programme
starts within one segment (~4s) of its target offset, the way real FAST
channels behave, and the reason the cycle can never accumulate drift (its
length is the sum of its actual segments, not a nominal number).
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from datetime import timedelta

from app.core.timeline import (
    Asset,
    ChannelNotStarted,
    NowPlaying,
    PlayoutSegment,
    ProgrammeBlock,
    Window,
)

EPS = 1e-6


@dataclass(frozen=True)
class ScheduleDef:
    """A validated schedule: programmes at offsets, plus looping filler."""

    entries: tuple[tuple[Asset, float], ...]  # (asset, target_offset_seconds), sorted
    filler: Asset
    period: float
    ad_breaks: tuple[tuple[float, float], ...] = ()  # (offset, duration)

    @staticmethod
    def build(entries, filler: Asset, period: float, ad_breaks=()) -> "ScheduleDef":
        ordered = tuple(sorted(entries, key=lambda e: e[1]))
        return ScheduleDef(
            entries=ordered,
            filler=filler,
            period=period,
            ad_breaks=tuple(sorted(ad_breaks)),
        )


def validate_schedule(
    entries: list[tuple[Asset, float]],
    filler: Asset | None,
    period: float,
    ad_breaks: list[tuple[float, float]] | None = None,
) -> list[str]:
    """Return human-readable violations; empty list means the schedule is valid.

    Shared by the HTTP endpoint and (later) the AI director, so both judge
    schedules by exactly the same rules.
    """
    errors: list[str] = []
    if filler is None:
        errors.append("a filler asset is required")
    if period <= 0:
        errors.append("period must be positive")
    ordered = sorted(entries, key=lambda e: e[1])
    prev_end = 0.0
    prev_title = None
    for asset, offset in ordered:
        if offset < -EPS:
            errors.append(f"'{asset.title}' has a negative start offset")
        if offset + asset.duration > period + EPS:
            errors.append(
                f"'{asset.title}' ends after the cycle period ({period:.0f}s)"
            )
        if offset + EPS < prev_end:
            errors.append(
                f"'{asset.title}' overlaps the previous entry"
                + (f" ('{prev_title}')" if prev_title else "")
            )
        prev_end = max(prev_end, offset + asset.duration)
        prev_title = asset.title
    for i, (offset, duration) in enumerate(ad_breaks or []):
        if duration <= 0:
            errors.append(f"ad break {i + 1} has a non-positive duration")
        if offset < -EPS or offset + duration > period + EPS:
            errors.append(f"ad break {i + 1} falls outside the cycle period")
    return errors


@dataclass(frozen=True)
class _CycleSegment:
    uri: str
    duration: float
    asset_id: str
    asset_title: str
    within_idx: int  # index within its source asset; drives continuity


@dataclass(frozen=True)
class _Block:
    asset_id: str
    title: str
    start_offset: float
    duration: float


class ScheduledTimeline:
    def __init__(self, schedule: ScheduleDef, epoch):
        self.epoch = epoch
        self.schedule = schedule
        self._cycle = self._build_cycle(schedule)
        if not self._cycle:
            raise ValueError("schedule produced an empty cycle")
        self._n = len(self._cycle)

        self._starts: list[float] = []
        offset = 0.0
        for seg in self._cycle:
            self._starts.append(offset)
            offset += seg.duration
        self.cycle_duration = offset
        self.target_duration = max(1, math.ceil(max(s.duration for s in self._cycle)))

        self._internal_disc, self._wrap_disc = self._discontinuity_flags()
        self._internal_total = sum(self._internal_disc)
        self._blocks, self._block_starts = self._build_blocks()
        self._cue_out, self._cue_in = self._build_cue_points(schedule.ad_breaks)

    def _build_cue_points(self, ad_breaks) -> tuple[dict[int, float], set[int]]:
        # Map each ad break to the cycle segment where the avail opens (CUE-OUT,
        # carrying the avail duration) and where content resumes (CUE-IN). These
        # recur every cycle, so an SSAI system sees the same avails each loop.
        cue_out: dict[int, float] = {}
        cue_in: set[int] = set()
        for offset, duration in ad_breaks:
            out_idx = bisect.bisect_left(self._starts, offset - EPS)
            if out_idx < self._n:
                cue_out[out_idx] = duration
            in_idx = bisect.bisect_left(self._starts, offset + duration - EPS)
            if in_idx < self._n:
                cue_in.add(in_idx)
        return cue_out, cue_in

    # -- construction ------------------------------------------------------

    def _build_cycle(self, sched: ScheduleDef) -> list[_CycleSegment]:
        cyc: list[_CycleSegment] = []
        cursor = 0.0
        fsegs = sched.filler.segments

        def lay_filler_until(limit: float) -> None:
            nonlocal cursor
            fi = 0
            while cursor + EPS < limit:
                seg = fsegs[fi % len(fsegs)]
                cyc.append(
                    _CycleSegment(
                        seg.uri,
                        seg.duration,
                        sched.filler.id,
                        sched.filler.title,
                        fi % len(fsegs),
                    )
                )
                cursor += seg.duration
                fi += 1

        for asset, target in sched.entries:
            if target > cursor + EPS:
                lay_filler_until(target)
            for i, seg in enumerate(asset.segments):
                cyc.append(
                    _CycleSegment(seg.uri, seg.duration, asset.id, asset.title, i)
                )
                cursor += seg.duration
        if sched.period > cursor + EPS:
            lay_filler_until(sched.period)
        return cyc

    def _continues(self, a: _CycleSegment, b: _CycleSegment) -> bool:
        # A segment continues the previous one only if it's the same asset and
        # the very next segment in that asset's natural order. Everything else
        # (asset change, filler loop restart) is a decode discontinuity.
        return a.asset_id == b.asset_id and b.within_idx == a.within_idx + 1

    def _discontinuity_flags(self) -> tuple[list[int], int]:
        n = self._n
        internal = [0] * n
        for i in range(1, n):
            internal[i] = 0 if self._continues(self._cycle[i - 1], self._cycle[i]) else 1
        wrap = 0 if self._continues(self._cycle[-1], self._cycle[0]) else 1
        return internal, wrap

    def _build_blocks(self) -> tuple[list[_Block], list[float]]:
        blocks: list[_Block] = []
        i = 0
        n = self._n
        while i < n:
            j = i + 1
            while j < n and self._continues(self._cycle[j - 1], self._cycle[j]):
                j += 1
            duration = sum(s.duration for s in self._cycle[i:j])
            blocks.append(
                _Block(
                    asset_id=self._cycle[i].asset_id,
                    title=self._cycle[i].asset_title,
                    start_offset=self._starts[i],
                    duration=duration,
                )
            )
            i = j
        return blocks, [b.start_offset for b in blocks]

    # -- resolution --------------------------------------------------------

    def _start_offset(self, g: int) -> float:
        cycle, idx = divmod(g, self._n)
        return cycle * self.cycle_duration + self._starts[idx]

    def _discontinuity(self, g: int) -> bool:
        if g == 0:
            return False
        idx = g % self._n
        if idx == 0:
            return bool(self._wrap_disc)
        return bool(self._internal_disc[idx])

    def _discontinuities_before(self, g: int) -> int:
        if g <= 0:
            return 0
        n = self._n
        full, rem = divmod(g, n)
        total = full * self._internal_total + sum(self._internal_disc[1:rem])
        total += ((g - 1) // n) * self._wrap_disc
        return total

    def current_index(self, now) -> int:
        position = (now - self.epoch).total_seconds()
        if position < 0:
            raise ChannelNotStarted(f"channel starts at {self.epoch.isoformat()}")
        cycle, in_cycle = divmod(position, self.cycle_duration)
        idx = bisect.bisect_right(self._starts, in_cycle) - 1
        return int(cycle) * self._n + idx

    def window(self, now, size: int = 5) -> Window:
        current = self.current_index(now)
        first = max(0, current - size + 1)
        segments = []
        for g in range(first, current + 1):
            idx = g % self._n
            c = self._cycle[idx]
            segments.append(
                PlayoutSegment(
                    uri=c.uri,
                    duration=c.duration,
                    asset_id=c.asset_id,
                    asset_title=c.asset_title,
                    discontinuity=self._discontinuity(g),
                    program_datetime=self.epoch
                    + timedelta(seconds=self._start_offset(g)),
                    cue_out=self._cue_out.get(idx),
                    cue_in=idx in self._cue_in,
                )
            )
        return Window(
            segments=tuple(segments),
            media_sequence=first,
            discontinuity_sequence=self._discontinuities_before(first),
            target_duration=self.target_duration,
        )

    def now_playing(self, now) -> NowPlaying:
        position = (now - self.epoch).total_seconds()
        if position < 0:
            raise ChannelNotStarted(f"channel starts at {self.epoch.isoformat()}")
        in_cycle = position % self.cycle_duration
        b = bisect.bisect_right(self._block_starts, in_cycle) - 1
        block = self._blocks[b]
        nxt = self._blocks[(b + 1) % len(self._blocks)]
        return NowPlaying(
            asset_id=block.asset_id,
            asset_title=block.title,
            asset_position=in_cycle - block.start_offset,
            asset_duration=block.duration,
            up_next_id=nxt.asset_id,
            up_next_title=nxt.title,
        )

    def programme_blocks(self) -> list[ProgrammeBlock]:
        # Merge consecutive same-asset segments (across filler loop restarts) so
        # a gap of looped filler shows as one guide entry, not many.
        blocks: list[ProgrammeBlock] = []
        i = 0
        n = self._n
        filler_id = self.schedule.filler.id
        while i < n:
            aid = self._cycle[i].asset_id
            title = self._cycle[i].asset_title
            start = self._starts[i]
            duration = 0.0
            while i < n and self._cycle[i].asset_id == aid:
                duration += self._cycle[i].duration
                i += 1
            blocks.append(
                ProgrammeBlock(aid, title, start, duration, is_filler=(aid == filler_id))
            )
        return blocks
