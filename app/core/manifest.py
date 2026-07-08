"""Render a playout window as an HLS media playlist (RFC 8216)."""

from __future__ import annotations

from datetime import timezone

from app.core.timeline import Window


def _pdt(dt) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def render_media_playlist(window: Window) -> str:
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{window.target_duration}",
        f"#EXT-X-MEDIA-SEQUENCE:{window.media_sequence}",
        f"#EXT-X-DISCONTINUITY-SEQUENCE:{window.discontinuity_sequence}",
    ]
    for seg in window.segments:
        if seg.discontinuity:
            lines.append("#EXT-X-DISCONTINUITY")
        if seg.cue_out is not None:
            lines.append(f"#EXT-X-CUE-OUT:{seg.cue_out:.3f}")
        if seg.cue_in:
            lines.append("#EXT-X-CUE-IN")
        lines.append(f"#EXT-X-PROGRAM-DATE-TIME:{_pdt(seg.program_datetime)}")
        lines.append(f"#EXTINF:{seg.duration:.3f},{seg.asset_title}")
        lines.append(seg.uri)
    return "\n".join(lines) + "\n"
