# Operations

How to configure, run, and scale showrunner.

## Configuration

All settings are environment variables (see `.env.example`).

### Channel & catalog
| Variable | Default | Purpose |
|---|---|---|
| `SHOWRUNNER_EPOCH` | `2026-01-01T00:00:00+00:00` | Global channel epoch (the instant the channel "went on air"; must be in the past) |
| `SHOWRUNNER_CATALOG_SOURCE` | `db` if `SHOWRUNNER_DB` set, else `json` | Where the channel reads assets: `db` (ingested) or `json` (demo file) |
| `SHOWRUNNER_CATALOG` | `data/catalog.json` | JSON catalog path (json mode) |
| `SHOWRUNNER_DB` | `sqlite:///data/showrunner.db` | Database URL (SQLite dev, Postgres prod) |
| `SHOWRUNNER_WINDOW` | `5` | Segments per live manifest window |
| `SHOWRUNNER_MANIFEST_TTL` | `1.0` | Manifest cache TTL (seconds) |

### Ingest & normalization
| Variable | Default | Purpose |
|---|---|---|
| `SHOWRUNNER_SEGMENT_DIR` | `data/segments` | Where segments live (local storage) |
| `SHOWRUNNER_UPLOAD_DIR` / `SHOWRUNNER_WORK_DIR` | `data/uploads` · `data/work` | Ingest scratch dirs |
| `SHOWRUNNER_MAX_UPLOAD_BYTES` | `4294967296` (4 GiB) | Max `/ingest` upload size; larger uploads get 413 |
| `SHOWRUNNER_WIDTH` / `HEIGHT` / `FPS` | `1280` · `720` · `25` | Uniform ladder video spec |
| `SHOWRUNNER_SEGMENT_SECONDS` | `4` | Target segment length |
| `SHOWRUNNER_AUDIO_BITRATE` | `128k` | Audio bitrate |

### Storage, queue, encode
| Variable | Default | Purpose |
|---|---|---|
| `SHOWRUNNER_STORAGE` | `local` | `local` or `s3` (MinIO/S3) |
| `SHOWRUNNER_S3_ENDPOINT` / `_BUCKET` / `_ACCESS_KEY` / `_SECRET_KEY` / `_REGION` | - | S3 backend |
| `SHOWRUNNER_API_KEY` | _(empty)_ | When set, POST/PUT/PATCH/DELETE require it as `X-API-Key` or `Authorization: Bearer`; GETs stay open |
| `SHOWRUNNER_CORS_ORIGINS` | `*` | Comma-separated allowed browser origins |
| `SHOWRUNNER_BROKER` | _(empty)_ | Celery broker URL; empty runs ingest in-process |
| `SHOWRUNNER_ENCODE_DIR` | `data/encoded` | True-encode output dir |
| `SHOWRUNNER_ENCODE_FONT` | _(auto)_ | Branding font path; auto-detected if unset |

The Go origin reads `SHOWRUNNER_SNAPSHOT` (single file) or
`SHOWRUNNER_SNAPSHOT_DIR` (per-channel `{id}.json`) and `SHOWRUNNER_ADDR`
(default `:8100`).

## Running

### Local (zero dependencies)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python scripts/make_demo_assets.py      # synthetic demo assets
uvicorn app.main:app --reload           # http://localhost:8000/
```
Defaults to SQLite + local disk + in-process ingest; no Postgres/Redis/MinIO.

### Full stack (Docker)
```bash
docker compose up                       # app only (demo catalog)
docker compose --profile full up        # + Postgres, Redis, MinIO, Celery worker
```
The `full` profile wires the worker to Postgres/Redis and segment storage to
MinIO; flip `SHOWRUNNER_CATALOG_SOURCE=db` to serve ingested content.

## Production topology

```
            ┌───────────────┐        ┌──────────────────────────┐
 viewers ──▶│  CDN / nginx  │──┬────▶│ origin (Python or Go) ×N │  manifests
            └───────────────┘  │     └──────────────────────────┘
                    │          └────▶ object store (S3/MinIO)      segments
                    ▼
            static segments (cached at the edge)
```

- **Segments** are static files; serve them from nginx or a CDN, never from
  the app. They're ~99.9% of the bytes and the app never needs to touch them.
- **Manifests** are tiny and cached 1s; front them with the same CDN
  (`Cache-Control: max-age=1` lets the edge collapse the fan-out).
- **Origins are stateless**: run as many replicas as you like behind the load
  balancer; they agree byte-for-byte. Use the Go origin for the highest
  throughput (below).
- **Control plane** (ingest, scheduling, the AI director) runs separately from
  the data plane and can scale independently; it's not on the viewer path.

- **Access control:** with `SHOWRUNNER_API_KEY` unset, every endpoint is open,
  which is only appropriate on localhost. Set the key and restrict
  `SHOWRUNNER_CORS_ORIGINS` before exposing the control plane. The key guards
  writes (ingest, schedule, AI director, encode, channel management); reads
  and the demo UI stay public so viewers need no credentials. Serve it over
  TLS, since the key travels in a header.

## Performance & the Go origin

The manifest hot path is reimplemented in Go (`go-origin/`) and validated
byte-for-byte against Python. To deploy it: compile snapshots with the control
plane, point the Go origin at them.

```bash
python scripts/export_snapshot.py --all /srv/snapshots   # one file per channel
cd go-origin && go build -o origin .
SHOWRUNNER_SNAPSHOT_DIR=/srv/snapshots ./origin           # serves /channel/{id}/playlist.m3u8
```

Re-export snapshots whenever a schedule changes (e.g. from a control-plane hook
after `PUT /schedule`).

### Benchmark
Same channel, one process each, `wrk -t4 -c64 -d10s` (Apple Silicon):

| Origin | Requests/sec | p50 latency |
|---|---:|---:|
| Python, FastAPI + uvicorn (1 worker) | ~8,900 | 7.6 ms |
| Go, net/http | ~147,000 | 0.5 ms |

Reproduce with `./scripts/bench.sh`. uvicorn scales with more workers (~1 per
core); the Go process scales across cores on its own.

## Verifying a channel

- **Soak (simulated)**: `pytest tests/test_soak.py` fast-forwards a full 24h
  through the resolver, asserting no gaps, monotonic sequences, exact
  `PROGRAM-DATE-TIME`, and zero drift.
- **Soak (live)**: `python scripts/soak.py --url http://localhost:8000
  --duration 300` polls a running server, measuring real drift and stalls and
  checking every referenced segment is fetchable. `--ffmpeg-check` also decodes
  the stream. `--duration 86400` for a full day.

## Tests & CI

```bash
pytest                       # Python suite (ffmpeg-gated tests skip without ffmpeg)
cd go-origin && go test ./...  # Go origin against the Python golden fixtures
```
CI runs both jobs on every push. If you change the manifest renderer, regenerate
the Go fixtures (`python scripts/gen_go_fixtures.py`) and commit them; a test
fails if they drift.

## Operational notes

- **Schema changes** (new columns) require a fresh database in dev; showrunner
  uses `create_all`, not migrations. Add Alembic before running a long-lived
  production database.
- **True-encode** needs segments on local disk (not S3) and an ffmpeg with
  `drawtext` for the channel-name overlay; without it, branding degrades to a
  plain bar automatically.
- **Epoch must be in the past**: a future epoch makes `/playlist.m3u8` return
  404 ("channel not started").
