# showrunner 📺

**An AI-programmed linear TV channel.** A cloud playout engine that turns a
media library into a 24/7 HLS live stream — with Claude as the programming
director.

> *"Program Saturday evening as a 90s action night, family-friendly until
> 21:00"* → a valid, gapless broadcast schedule.

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
| `GET /segments/{asset}/{n}.ts` | Segment delivery (nginx/CDN in production) |
| `GET /demo/` | hls.js demo player |

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

## Tests

```bash
pytest -v
```

The test suite covers the timeline resolver's edge cases — asset joins, cycle
wrap, discontinuity sequencing, drift over 1000 cycles — because that math is
where playout engines live or die.

## Status

Phase 3 (programme scheduler, 53 tests) complete — see the
[roadmap](PLAN.md#5-phased-roadmap). Next: the 24-hour soak test.

## License

MIT
