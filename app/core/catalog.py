"""Load the asset catalog produced at ingest time.

Phase 0 uses a JSON file written by scripts/make_demo_assets.py.
Phase 2 replaces this with Postgres-backed catalog rows; the Asset/Segment
dataclasses are the stable contract.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core.timeline import Asset, Segment


class CatalogNotFound(Exception):
    pass


def load_catalog(path: str | Path) -> list[Asset]:
    p = Path(path)
    if not p.exists():
        raise CatalogNotFound(
            f"no catalog at {p} — run `python scripts/make_demo_assets.py` first"
        )
    raw = json.loads(p.read_text())
    assets = []
    for a in raw["assets"]:
        segments = tuple(
            Segment(uri=s["uri"], duration=float(s["duration"]))
            for s in a["segments"]
        )
        if not segments:
            raise ValueError(f"asset {a['id']} has no segments")
        assets.append(Asset(id=a["id"], title=a["title"], segments=segments))
    return assets
