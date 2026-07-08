"""Timeline resolution: map wall-clock time onto the channel's playout position.

Everything here is a pure function of (epoch, catalog, wall clock). No state,
no ticking — any number of replicas compute the identical answer, and a
restart can never drift the channel.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class Segment:
    uri: str
    duration: float


@dataclass(frozen=True)
class Asset:
    id: str
    title: str
    segments: tuple[Segment, ...]

    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.segments)


@dataclass(frozen=True)
class PlayoutSegment:
    uri: str
    duration: float
    asset_id: str
    asset_title: str
    discontinuity: bool
    program_datetime: datetime


@dataclass(frozen=True)
class Window:
    segments: tuple[PlayoutSegment, ...]
    media_sequence: int
    discontinuity_sequence: int
    target_duration: int


@dataclass(frozen=True)
class NowPlaying:
    asset_id: str
    asset_title: str
    asset_position: float
    asset_duration: float
    up_next_id: str
    up_next_title: str


class ChannelNotStarted(Exception):
    """Requested time is before the channel epoch."""


class LoopingTimeline:
    """Phase-1 scheduler: plays the asset list on repeat from a fixed epoch.

    Later phases swap this for a Postgres-backed schedule; the Window
    contract stays the same so the manifest generator never changes.
    """

    def __init__(self, assets: list[Asset], epoch: datetime):
        if not assets:
            raise ValueError("timeline needs at least one asset")
        if epoch.tzinfo is None:
            raise ValueError("epoch must be timezone-aware")
        self.assets = list(assets)
        self.epoch = epoch

        # Flatten one full cycle into (asset, segment, starts_new_asset).
        self._flat: list[tuple[Asset, Segment, bool]] = []
        for asset in self.assets:
            for i, seg in enumerate(asset.segments):
                self._flat.append((asset, seg, i == 0))
        self._n = len(self._flat)

        # Prefix sums of segment start offsets within one cycle.
        self._starts: list[float] = []
        offset = 0.0
        for _, seg, _ in self._flat:
            self._starts.append(offset)
            offset += seg.duration
        self.cycle_duration = offset

        # Asset joins per cycle. Every asset start is a join once the
        # channel loops (last asset wraps back to the first).
        self._joins_per_cycle = sum(1 for _, _, s in self._flat if s)
        # F(r): joins among flat indices [0, r), counting index 0.
        self._joins_prefix = [0] * (self._n + 1)
        for i, (_, _, s) in enumerate(self._flat):
            self._joins_prefix[i + 1] = self._joins_prefix[i] + (1 if s else 0)

        self.target_duration = max(
            1, math.ceil(max(seg.duration for _, seg, _ in self._flat))
        )

    # -- global segment index helpers -------------------------------------

    def _start_offset(self, g: int) -> float:
        cycle, idx = divmod(g, self._n)
        return cycle * self.cycle_duration + self._starts[idx]

    def _discontinuity(self, g: int) -> bool:
        _, _, starts_new = self._flat[g % self._n]
        return starts_new and g != 0

    def _discontinuities_before(self, g: int) -> int:
        if g <= 0:
            return 0
        cycle, idx = divmod(g, self._n)
        naive = cycle * self._joins_per_cycle + self._joins_prefix[idx]
        # The very first segment of the stream is not a discontinuity,
        # but the naive count includes it.
        return naive - 1

    def current_index(self, now: datetime) -> int:
        position = (now - self.epoch).total_seconds()
        if position < 0:
            raise ChannelNotStarted(
                f"channel starts at {self.epoch.isoformat()}"
            )
        cycle, in_cycle = divmod(position, self.cycle_duration)
        idx = bisect.bisect_right(self._starts, in_cycle) - 1
        return int(cycle) * self._n + idx

    # -- public API --------------------------------------------------------

    def window(self, now: datetime, size: int = 5) -> Window:
        """Return the live manifest window ending at the current segment."""
        current = self.current_index(now)
        first = max(0, current - size + 1)
        segments = []
        for g in range(first, current + 1):
            asset, seg, _ = self._flat[g % self._n]
            segments.append(
                PlayoutSegment(
                    uri=seg.uri,
                    duration=seg.duration,
                    asset_id=asset.id,
                    asset_title=asset.title,
                    discontinuity=self._discontinuity(g),
                    program_datetime=self.epoch
                    + timedelta(seconds=self._start_offset(g)),
                )
            )
        return Window(
            segments=tuple(segments),
            media_sequence=first,
            discontinuity_sequence=self._discontinuities_before(first),
            target_duration=self.target_duration,
        )

    def now_playing(self, now: datetime) -> NowPlaying:
        g = self.current_index(now)
        asset, _, _ = self._flat[g % self._n]
        position = (now - self.epoch).total_seconds()
        in_cycle = position % self.cycle_duration
        asset_start = 0.0
        for a in self.assets:
            if a.id == asset.id:
                break
            asset_start += a.duration
        idx = self.assets.index(asset)
        up_next = self.assets[(idx + 1) % len(self.assets)]
        return NowPlaying(
            asset_id=asset.id,
            asset_title=asset.title,
            asset_position=in_cycle - asset_start,
            asset_duration=asset.duration,
            up_next_id=up_next.id,
            up_next_title=up_next.title,
        )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
