import os
from datetime import datetime, timezone

CATALOG_PATH = os.environ.get("SHOWRUNNER_CATALOG", "data/catalog.json")
SEGMENT_DIR = os.environ.get("SHOWRUNNER_SEGMENT_DIR", "data/segments")
WINDOW_SIZE = int(os.environ.get("SHOWRUNNER_WINDOW", "5"))
MANIFEST_CACHE_TTL = float(os.environ.get("SHOWRUNNER_MANIFEST_TTL", "1.0"))

_epoch_raw = os.environ.get("SHOWRUNNER_EPOCH", "2026-01-01T00:00:00+00:00")
CHANNEL_EPOCH = datetime.fromisoformat(_epoch_raw)
if CHANNEL_EPOCH.tzinfo is None:
    CHANNEL_EPOCH = CHANNEL_EPOCH.replace(tzinfo=timezone.utc)
