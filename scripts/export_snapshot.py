#!/usr/bin/env python3
"""Export the current channel as a snapshot the Go origin can serve.

Builds the exact same timeline the FastAPI app would serve (honouring
SHOWRUNNER_CATALOG_SOURCE / schedule / demo catalog) and writes its snapshot,
so the Go origin serves a byte-identical channel.

    python scripts/export_snapshot.py [output.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.snapshot import export_snapshot  # noqa: E402
from app.routers.channel import _build_timeline  # noqa: E402


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else "-"
    snapshot = export_snapshot(_build_timeline())
    text = json.dumps(snapshot, indent=2)
    if out == "-":
        print(text)
    else:
        Path(out).write_text(text + "\n")
        print(f"wrote {out} ({len(snapshot['segments'])} segments)", file=sys.stderr)


if __name__ == "__main__":
    main()
