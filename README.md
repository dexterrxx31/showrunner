# showrunner 📺

**An AI-programmed linear TV channel.** A cloud playout engine that turns a
media library into a 24/7 HLS live stream — with Claude as the programming
director.

> *"Program Saturday evening as a 90s action night, family-friendly until
> 21:00"* → a valid, gapless broadcast schedule.

![The demo channel on air](docs/demo.gif)

*The live channel cycling through synthetic test-pattern assets — a continuous
HLS stream assembled from pre-cut segments with a discontinuity at each join.*

## How it works

No video is processed at playout time. Assets are normalized to one uniform
spec and pre-cut into HLS segments at ingest; the "channel" is a **stateless
manifest generator** that maps wall clock + schedule onto pre-cut segments —
the same manifest-stitching architecture behind most FAST channels
(Pluto TV-style). Because every manifest is a pure function of
`(epoch, catalog, schedule, wall clock)`, replicas agree byte-for-byte,
restarts can't drift the channel, and horizontal scaling is free.

See [PLAN.md](PLAN.md) for the full architecture, roadmap, and design notes.

## Quickstart

Requires Python 3.12+ and FFmpeg.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# Generate synthetic demo assets (FFmpeg test sources — no real media needed)
python scripts/make_demo_assets.py

# Start the channel
uvicorn app.main:app --reload
```

Then open **http://localhost:8000/** for the demo player, or point VLC at
`http://localhost:8000/channel/demo/playlist.m3u8`.

Or with Docker:

```bash
docker compose up --build
```

## Endpoints

| Endpoint | Description |
|---|---|
| `GET /channel/demo/playlist.m3u8` | Live HLS media playlist (sliding window) |
| `GET /channel/demo/now` | On-air / up-next JSON |
| `POST /ingest` | Upload a video (multipart: `file`, `title`) → normalize + segment |
| `GET /ingest/{job_id}` | Ingest job status |
| `GET/PUT/DELETE /schedule` | Read / replace / clear the programme schedule |
| `POST /schedule/generate` | AI director: brief → validated schedule |
| `GET/POST /channels`, `DELETE /channels/{id}` | Manage channels; per-channel `/channels/{id}/schedule` too |
| `GET /assets`, `PATCH /assets/{id}` | List assets / set genre, rating, year |
| `POST /channel/{id}/encode/{start,stop}` | True-encode: continuous branded FFmpeg playout |
| `GET /epg.xml` | Electronic programme guide (XMLTV) |
| `GET /epg.json` | Programme guide as JSON (used by the demo UI) |
| `GET /segments/{asset}/{n}.ts` | Segment delivery (nginx/CDN in production) |
| `GET /demo/` | hls.js demo player with live on-air bar + guide |

## Ingesting your own video

By default the channel plays the JSON demo catalog. To ingest real files into
the database-backed catalog and have the channel air them:

```bash
export SHOWRUNNER_CATALOG_SOURCE=db      # channel reads ingested assets
uvicorn app.main:app --reload

curl -F "title=Late Night Movie" -F "file=@movie.mp4" http://localhost:8000/ingest
# → {"job_id": "...", "asset_id": "late-night-movie-xxxxxxxx", "status": "pending"}
```

Ingest runs in-process by default (no broker needed). Set `SHOWRUNNER_BROKER`
to route it to a Celery worker, and `SHOWRUNNER_STORAGE=s3` to store segments
in MinIO/S3 — see `docker compose --profile full up`.

## Programming a schedule

Without a schedule the channel simply loops the whole catalog. Define a
programme schedule — assets at target offsets within a repeating cycle, with a
filler asset looping to cover every gap:

```bash
curl -X PUT http://localhost:8000/schedule -H 'content-type: application/json' -d '{
  "filler_asset_id": "ident-xxxxxxxx",
  "period_seconds": 3600,
  "entries": [
    {"asset_id": "movie-xxxxxxxx", "start_offset": 1200}
  ]
}'
```

The schedule is validated before it's stored (no overlaps, nothing past the
period, filler required) and takes effect immediately. Timing is
segment-accurate: a programme starts within one segment (~4s) of its target,
which is what keeps the cycle drift-free. `DELETE /schedule` reverts to
looping the catalog.

Add `ad_breaks` (offset + duration within the cycle) to emit
`EXT-X-CUE-OUT`/`EXT-X-CUE-IN` markers in the manifest — the ad avails an
SSAI/SCTE-35 system replaces with real ads:

```json
{ "filler_asset_id": "ident-...", "period_seconds": 3600,
  "entries": [{"asset_id": "movie-...", "start_offset": 1200}],
  "ad_breaks": [{"start_offset": 1180, "duration": 30}] }
```

## Tests

```bash
pytest -v
```

The test suite covers the timeline resolver's edge cases — asset joins, cycle
wrap, discontinuity sequencing, drift over 1000 cycles — because that math is
where playout engines live or die.

## AI programming director

Instead of hand-writing a schedule, describe what you want and let Claude
build it. Set `ANTHROPIC_API_KEY`, tag assets with metadata, then:

```bash
curl -X PATCH http://localhost:8000/assets/movie-xxxx \
  -H 'content-type: application/json' \
  -d '{"genre": "action", "rating": "PG-13", "year": 1994}'

curl -X POST http://localhost:8000/schedule/generate \
  -H 'content-type: application/json' \
  -d '{"brief": "A 90s action night, family-friendly until 21:00, idents between films", "period_seconds": 10800}'
```

Claude inspects the catalog via tool calls and submits a schedule; the server
validates it with the same rules as `PUT /schedule` and hands back any
violations for Claude to fix. The engine only ever airs a schedule that passed
validation — the model proposes, deterministic validation disposes. The
accepted schedule is persisted and goes live immediately.

## Multiple channels

One asset library can drive many independent channels — each with its own name,
epoch, schedule, and ad breaks:

```bash
curl -X POST http://localhost:8000/channels -d '{"id": "news", "name": "News 24"}'
curl -X PUT http://localhost:8000/channels/news/schedule -d '{ ... }'
# → http://localhost:8000/channel/news/playlist.m3u8
```

Every channel gets its own manifest, EPG, and (optionally) AI-generated
schedule. The Go origin serves them all from a directory of snapshots
(`python scripts/export_snapshot.py --all <dir>`).

## True-encode mode (optional)

Manifest-stitching is the default (stateless, restart-safe). For channels that
need **burned-in graphics** — a channel bug, a lower-third — there's an
alternative that runs a continuous FFmpeg process re-encoding the channel into
one branded stream, the way a traditional playout chain works:

```bash
curl -X POST http://localhost:8000/channel/demo/encode/start
# → serves /encoded/demo/playlist.m3u8 with the channel name burned in
curl -X POST http://localhost:8000/channel/demo/encode/stop
```

It trades the stateless properties for frame-accurate on-screen graphics, and
degrades to a plain lower-third bar if the local ffmpeg lacks `drawtext`.

## Performance: the Go manifest origin

The manifest is the hot path — every viewer polls it every few seconds — but
it's a pure function of the wall clock, so it can be served by a stateless
origin with no database or scheduler. `showrunner` splits along that line:

- **Control plane (Python)** owns the hard, infrequent work — ingest,
  scheduling, discontinuity resolution — and compiles the schedule into a
  **cycle snapshot** (`python scripts/export_snapshot.py`).
- **Data plane (Go, `go-origin/`)** serves manifests from the snapshot with
  pure arithmetic, re-implementing only `render_media_playlist`.

The Go origin is validated **byte-for-byte** against the canonical Python
renderer: `scripts/gen_go_fixtures.py` emits 126 golden manifests from the
Python code, and `go test` asserts the Go output matches every one.

Throughput serving the same channel (`wrk -t4 -c64 -d10s`, Apple Silicon, one
process each):

| Origin | Requests/sec | p50 latency |
|---|---:|---:|
| Python — FastAPI + uvicorn (1 worker) | ~8,900 | 7.6 ms |
| Go — net/http | ~147,000 | 0.5 ms |

**~16× throughput**, reproducible with `./scripts/bench.sh`. (uvicorn scales
with more workers, ~1 per core; the Go process scales across cores on its own.)

## Soak testing

The channel is a pure function of the wall clock, so 24 hours of playout can be
verified deterministically — `tests/test_soak.py` fast-forwards a full day and
asserts no gaps, monotonic sequences, exact PROGRAM-DATE-TIME continuity, and a
live edge that always tracks real time (zero drift).

To soak a running server over real time:

```bash
python scripts/soak.py --url http://localhost:8000 --duration 300
python scripts/soak.py --duration 86400 --ffmpeg-check   # full 24h + decode
```

It reports drift, stalls, and discontinuities, verifies every referenced
segment is fetchable, and exits non-zero on any violation.

## Status

All seven phases of the [roadmap](PLAN.md#5-phased-roadmap) complete — from a
fake-live spike to an AI-programmed channel with a Go manifest origin. 81
Python tests + a Go golden test, CI green on both.

## License

MIT
