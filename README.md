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
| `GET /segments/{asset}/{n}.ts` | Segment delivery (nginx/CDN in production) |
| `GET /demo/` | hls.js demo player |

## Tests

```bash
pytest -v
```

The test suite covers the timeline resolver's edge cases — asset joins, cycle
wrap, discontinuity sequencing, drift over 1000 cycles — because that math is
where playout engines live or die.

## Status

Phase 1 (hardened core, 26 tests) complete — see the
[roadmap](PLAN.md#5-phased-roadmap). Next: the ingest pipeline.

## License

MIT
