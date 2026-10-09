import os
from datetime import datetime, timezone

# --- channel ---------------------------------------------------------------
CATALOG_PATH = os.environ.get("SHOWRUNNER_CATALOG", "data/catalog.json")
SEGMENT_DIR = os.environ.get("SHOWRUNNER_SEGMENT_DIR", "data/segments")
WINDOW_SIZE = int(os.environ.get("SHOWRUNNER_WINDOW", "5"))
MANIFEST_CACHE_TTL = float(os.environ.get("SHOWRUNNER_MANIFEST_TTL", "1.0"))

# Where the channel reads its asset list: "json" (Phase 0/1 demo file) or
# "db" (Phase 2 ingested catalog). Defaults to db when a database is set.
DATABASE_URL = os.environ.get("SHOWRUNNER_DB", "sqlite:///data/showrunner.db")
CATALOG_SOURCE = os.environ.get(
    "SHOWRUNNER_CATALOG_SOURCE", "db" if os.environ.get("SHOWRUNNER_DB") else "json"
)

_epoch_raw = os.environ.get("SHOWRUNNER_EPOCH", "2026-01-01T00:00:00+00:00")
CHANNEL_EPOCH = datetime.fromisoformat(_epoch_raw)
if CHANNEL_EPOCH.tzinfo is None:
    CHANNEL_EPOCH = CHANNEL_EPOCH.replace(tzinfo=timezone.utc)

# --- access control ---------------------------------------------------------
# When set, POST/PUT/PATCH/DELETE require this key (X-API-Key or Bearer).
API_KEY = os.environ.get("SHOWRUNNER_API_KEY", "")
CORS_ORIGINS = [
    o.strip() for o in os.environ.get("SHOWRUNNER_CORS_ORIGINS", "*").split(",") if o.strip()
]

# --- ingest ----------------------------------------------------------------
UPLOAD_DIR = os.environ.get("SHOWRUNNER_UPLOAD_DIR", "data/uploads")
WORK_DIR = os.environ.get("SHOWRUNNER_WORK_DIR", "data/work")
MAX_UPLOAD_BYTES = int(os.environ.get("SHOWRUNNER_MAX_UPLOAD_BYTES", str(4 * 1024**3)))

# Uniform normalization ladder: every asset is transcoded to this exact spec
# so segments from different assets are interchangeable at playout time.
NORMALIZE_WIDTH = int(os.environ.get("SHOWRUNNER_WIDTH", "1280"))
NORMALIZE_HEIGHT = int(os.environ.get("SHOWRUNNER_HEIGHT", "720"))
NORMALIZE_FPS = int(os.environ.get("SHOWRUNNER_FPS", "25"))
SEGMENT_SECONDS = int(os.environ.get("SHOWRUNNER_SEGMENT_SECONDS", "4"))
AUDIO_BITRATE = os.environ.get("SHOWRUNNER_AUDIO_BITRATE", "128k")

# --- storage ---------------------------------------------------------------
# "local" writes segments under SEGMENT_DIR; "s3" uploads to a bucket (MinIO/S3).
STORAGE_BACKEND = os.environ.get("SHOWRUNNER_STORAGE", "local")
S3_BUCKET = os.environ.get("SHOWRUNNER_S3_BUCKET", "segments")
S3_ENDPOINT = os.environ.get("SHOWRUNNER_S3_ENDPOINT")  # e.g. http://minio:9000
S3_ACCESS_KEY = os.environ.get("SHOWRUNNER_S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.environ.get("SHOWRUNNER_S3_SECRET_KEY", "")
S3_REGION = os.environ.get("SHOWRUNNER_S3_REGION", "us-east-1")

# --- true-encode playout (optional alternative to manifest stitching) -------
ENCODE_DIR = os.environ.get("SHOWRUNNER_ENCODE_DIR", "data/encoded")
ENCODE_FONT = os.environ.get("SHOWRUNNER_ENCODE_FONT")  # branding font; auto if unset

# --- task queue ------------------------------------------------------------
# When set, ingest runs on a Celery worker; otherwise it runs eagerly in-process
# via FastAPI background tasks (fine for dev and tests, no broker required).
CELERY_BROKER_URL = os.environ.get("SHOWRUNNER_BROKER", "")
