#!/usr/bin/env python3
"""Export channel(s) as snapshot(s) the Go origin can serve.

Builds the exact timeline the FastAPI app serves for a channel and writes its
snapshot, so the Go origin serves a byte-identical channel.

    python scripts/export_snapshot.py [output.json] [channel_id]   # one channel
    python scripts/export_snapshot.py --all <dir>                  # every channel
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.snapshot import export_snapshot  # noqa: E402
from app.routers.channel import _build_timeline  # noqa: E402


def _write(path: Path, channel_id: str) -> None:
    snapshot = export_snapshot(_build_timeline(channel_id))
    path.write_text(json.dumps(snapshot, indent=2) + "\n")
    print(f"wrote {path} ({len(snapshot['segments'])} segments)", file=sys.stderr)


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--all":
        out_dir = Path(args[1])
        out_dir.mkdir(parents=True, exist_ok=True)
        from app.db import session_scope
        from app.models import ChannelSettingsRow

        with session_scope() as s:
            ids = [r.id for r in s.query(ChannelSettingsRow).all()] or ["demo"]
        for channel_id in ids:
            _write(out_dir / f"{channel_id}.json", channel_id)
        return

    out = args[0] if args else "-"
    channel_id = args[1] if len(args) > 1 else "demo"
    if out == "-":
        print(json.dumps(export_snapshot(_build_timeline(channel_id)), indent=2))
    else:
        _write(Path(out), channel_id)


if __name__ == "__main__":
    main()
