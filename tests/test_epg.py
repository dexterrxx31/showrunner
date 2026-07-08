"""EPG projection: cyclic schedule → absolute programmes, XMLTV and JSON."""

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from app.core.epg import build_epg_json, build_xmltv, programmes_in_window
from app.core.schedule import ScheduleDef, ScheduledTimeline
from app.core.timeline import Asset, LoopingTimeline, Segment

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def asset(aid, n, d=4.0):
    return Asset(
        id=aid,
        title=aid.upper(),
        segments=tuple(
            Segment(uri=f"/segments/{aid}/{i:05d}.ts", duration=d) for i in range(n)
        ),
    )


def scheduled():
    # movie 0-12s, filler fills 12-60s → two programme blocks per 60s cycle.
    return ScheduledTimeline(
        ScheduleDef.build([(asset("movie", 3), 0.0)], asset("ident", 1), 60.0),
        epoch=EPOCH,
    )


def test_programme_blocks_merge_filler_runs():
    blocks = scheduled().programme_blocks()
    assert [(b.asset_id, b.is_filler) for b in blocks] == [
        ("movie", False),
        ("ident", True),  # the whole 12-60s filler gap is one guide entry
    ]
    assert blocks[0].duration == 12.0
    assert blocks[1].start_offset == 12.0


def test_projection_repeats_across_cycles():
    tl = scheduled()
    progs = programmes_in_window(tl, EPOCH, hours=2 / 60)  # 2-minute window = 2 cycles
    titles = [b.title for _, _, b in progs]
    assert titles == ["MOVIE", "IDENT", "MOVIE", "IDENT"]
    # Second cycle's movie starts exactly one cycle after the first.
    starts = [s for s, _, _ in progs]
    assert (starts[2] - starts[0]).total_seconds() == tl.cycle_duration


def test_xmltv_is_wellformed_and_labels_filler():
    xml = build_xmltv(scheduled(), EPOCH, hours=2 / 60)
    root = ET.fromstring(xml)
    assert root.tag == "tv"
    assert root.find("channel").get("id") == "demo"
    programmes = root.findall("programme")
    assert len(programmes) == 4
    assert programmes[0].find("title").text == "MOVIE"
    assert programmes[0].get("start").endswith("+0000")
    # Filler entries carry a category tag.
    filler = [p for p in programmes if p.find("title").text == "IDENT"]
    assert all(p.find("category") is not None for p in filler)


def test_epg_json_shape():
    rows = build_epg_json(scheduled(), EPOCH, hours=1 / 60)  # one cycle
    assert rows[0]["title"] == "MOVIE"
    assert rows[0]["filler"] is False
    assert rows[1]["filler"] is True
    assert rows[0]["start"] == EPOCH.isoformat()


def test_epg_works_for_looping_timeline():
    tl = LoopingTimeline([asset("a", 3), asset("b", 2)], epoch=EPOCH)
    rows = build_epg_json(tl, EPOCH, hours=1 / 60)
    assert [r["title"] for r in rows[:2]] == ["A", "B"]
    assert all(r["filler"] is False for r in rows)
