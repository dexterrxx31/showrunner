"""Electronic programme guide — project the cyclic schedule onto real time.

The channel's `programme_blocks()` describe one cycle; the EPG repeats them
across the requested window and stamps absolute start/stop times. Output is
XMLTV (the format PVRs, TV apps, and set-top boxes consume) and a JSON view
for the demo UI.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta


def programmes_in_window(timeline, now: datetime, hours: float):
    """Yield (start, stop, ProgrammeBlock) across [now, now + hours)."""
    epoch = timeline.epoch
    cycle = timeline.cycle_duration
    blocks = timeline.programme_blocks()

    start_pos = max(0.0, (now - epoch).total_seconds())
    end_pos = (now - epoch).total_seconds() + hours * 3600
    out = []
    cycle_idx = int(start_pos // cycle)
    while cycle_idx * cycle <= end_pos:
        base = cycle_idx * cycle
        for b in blocks:
            s = base + b.start_offset
            e = s + b.duration
            if e <= start_pos or s >= end_pos:
                continue
            out.append(
                (
                    epoch + timedelta(seconds=s),
                    epoch + timedelta(seconds=e),
                    b,
                )
            )
        cycle_idx += 1
    return out


def build_xmltv(
    timeline,
    now: datetime,
    hours: float,
    channel_id: str = "demo",
    channel_name: str = "Showrunner Demo",
) -> str:
    tv = ET.Element("tv", {"generator-info-name": "showrunner"})
    channel = ET.SubElement(tv, "channel", {"id": channel_id})
    ET.SubElement(channel, "display-name").text = channel_name

    for start, stop, block in programmes_in_window(timeline, now, hours):
        programme = ET.SubElement(
            tv,
            "programme",
            {
                "start": start.strftime("%Y%m%d%H%M%S %z"),
                "stop": stop.strftime("%Y%m%d%H%M%S %z"),
                "channel": channel_id,
            },
        )
        ET.SubElement(programme, "title").text = block.title
        if block.is_filler:
            ET.SubElement(programme, "category").text = "filler"

    return ET.tostring(tv, encoding="unicode", xml_declaration=True)


def build_epg_json(timeline, now: datetime, hours: float) -> list[dict]:
    return [
        {
            "start": start.isoformat(),
            "stop": stop.isoformat(),
            "title": block.title,
            "asset_id": block.asset_id,
            "filler": block.is_filler,
        }
        for start, stop, block in programmes_in_window(timeline, now, hours)
    ]
