from datetime import datetime, timedelta, timezone

from app.core.manifest import render_media_playlist
from app.core.timeline import Asset, LoopingTimeline, Segment

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def build_window(at_seconds: float, size: int = 5):
    x = Asset(
        id="x",
        title="Asset X",
        segments=tuple(
            Segment(uri=f"/segments/x/{i:05d}.ts", duration=4.0) for i in range(3)
        ),
    )
    y = Asset(
        id="y",
        title="Asset Y",
        segments=tuple(
            Segment(uri=f"/segments/y/{i:05d}.ts", duration=4.0) for i in range(2)
        ),
    )
    tl = LoopingTimeline([x, y], epoch=EPOCH)
    return tl.window(EPOCH + timedelta(seconds=at_seconds), size=size)


def test_header_fields():
    m3u8 = render_media_playlist(build_window(13))
    lines = m3u8.strip().split("\n")
    assert lines[0] == "#EXTM3U"
    assert "#EXT-X-VERSION:3" in lines
    assert "#EXT-X-TARGETDURATION:4" in lines
    assert "#EXT-X-MEDIA-SEQUENCE:0" in lines
    assert "#EXT-X-DISCONTINUITY-SEQUENCE:0" in lines


def test_no_endlist_tag_live():
    assert "#EXT-X-ENDLIST" not in render_media_playlist(build_window(13))


def test_discontinuity_precedes_join_segment():
    m3u8 = render_media_playlist(build_window(13))
    lines = m3u8.strip().split("\n")
    disc = lines.index("#EXT-X-DISCONTINUITY")
    # The tag must be immediately followed by the joining segment's metadata.
    assert lines[disc + 1].startswith("#EXT-X-PROGRAM-DATE-TIME:")
    assert lines[disc + 2].startswith("#EXTINF:")
    assert lines[disc + 3] == "/segments/y/00000.ts"


def test_extinf_and_uri_pairing():
    m3u8 = render_media_playlist(build_window(13))
    lines = m3u8.strip().split("\n")
    extinf_count = sum(1 for l in lines if l.startswith("#EXTINF:"))
    uri_count = sum(1 for l in lines if l.startswith("/segments/"))
    assert extinf_count == uri_count == 4


def test_program_date_time_format():
    m3u8 = render_media_playlist(build_window(0, size=1))
    pdt = next(
        l for l in m3u8.split("\n") if l.startswith("#EXT-X-PROGRAM-DATE-TIME:")
    )
    assert pdt == "#EXT-X-PROGRAM-DATE-TIME:2026-01-01T00:00:00.000Z"
