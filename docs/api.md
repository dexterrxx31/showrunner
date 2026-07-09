# API reference

Base URL defaults to `http://localhost:8000`. All request/response bodies are
JSON unless noted. The channel-facing routes (`/channel/...`) are the public
data plane; the rest are the control plane.

Times are UTC ISO 8601. Offsets and durations are in seconds.

---

## Channel (playback)

### `GET /channel/{channel_id}/playlist.m3u8`
The live HLS media playlist — a sliding window over the channel's segments.
Cached 1 second. `channel_id` is `demo` unless you created others.

- `200` → `application/vnd.apple.mpegurl`, `Cache-Control: max-age=1`
- `404` → channel hasn't started (wall clock precedes its epoch)
- `503` → no catalog yet (nothing ingested)

### `GET /channel/{channel_id}/now`
What's on air and up next.

```json
{
  "on_air": {"asset_id": "movie-ab12", "title": "The Feature",
             "position_s": 42.1, "duration_s": 5400.0},
  "up_next": {"asset_id": "ident-9f0", "title": "Channel Ident"}
}
```

### `GET /channel/{channel_id}/epg.xml` · `GET /channel/{channel_id}/epg.json`
Electronic programme guide projected onto absolute time. Query: `hours`
(default 6, max 168). `.xml` is [XMLTV](http://wiki.xmltv.org/); `.json` is the
same data for UIs. Legacy aliases `GET /epg.xml` / `GET /epg.json` target `demo`.

```jsonc
// epg.json
[{"start": "2026-07-09T20:00:00+00:00", "stop": "2026-07-09T21:30:00+00:00",
  "title": "The Feature", "asset_id": "movie-ab12", "filler": false}]
```

---

## Ingest

### `POST /ingest`
Upload a source video; it's normalized to the uniform ladder, segmented, and
registered. `multipart/form-data`: `file` (the video), `title` (string).

- `202` → `{"job_id": "...", "asset_id": "the-title-ab12cd34", "status": "pending"}`

Runs in-process by default; set `SHOWRUNNER_BROKER` to route to a Celery worker.

### `GET /ingest/{job_id}`
Job status: `pending` → `running` → `done` (or `error`).

```json
{"job_id": "...", "asset_id": "...", "title": "...",
 "status": "done", "segment_count": 1350, "error": null}
```

---

## Assets

### `GET /assets`
List the catalog with metadata.

```json
[{"asset_id": "movie-ab12", "title": "The Feature", "duration_seconds": 5400.0,
  "genre": "action", "rating": "PG-13", "year": 1994}]
```

### `PATCH /assets/{asset_id}`
Set programming metadata (any subset). Used to ground AI-director briefs.

```json
{"genre": "action", "rating": "PG-13", "year": 1994}
```
- `404` → unknown asset

---

## Channels

### `GET /channels`
```json
[{"id": "demo", "name": null, "epoch": null, "has_schedule": true},
 {"id": "news", "name": "News 24", "epoch": "2026-01-01T00:00:00+00:00",
  "has_schedule": false}]
```

### `POST /channels`
Create a channel. `epoch` is optional (defaults to the global epoch).
```json
{"id": "news", "name": "News 24", "epoch": "2026-01-01T00:00:00+00:00"}
```
- `201` on success · `409` if the id exists · `422` if `epoch` isn't ISO 8601

### `DELETE /channels/{channel_id}`
Removes the channel and its schedule. The id still resolves afterward (it falls
back to looping the shared catalog).

---

## Schedule

Per channel: `/channels/{channel_id}/schedule`. Legacy single-channel aliases
(`/schedule`) target `demo`.

### `GET /channels/{channel_id}/schedule`
```json
{"channel_id": "demo", "filler_asset_id": "ident-9f0", "period_seconds": 3600,
 "entries": [{"asset_id": "movie-ab12", "title": "The Feature", "start_offset": 1200}],
 "ad_breaks": [{"start_offset": 1180, "duration": 30}]}
```

### `PUT /channels/{channel_id}/schedule`
Replace the schedule atomically. Validated before persisting.
```json
{"filler_asset_id": "ident-9f0", "period_seconds": 3600,
 "entries": [{"asset_id": "movie-ab12", "start_offset": 1200}],
 "ad_breaks": [{"start_offset": 1180, "duration": 30}]}
```
- `200` → `{"status": "ok", "entries": 1, "ad_breaks": 1}`
- `422` → validation errors (unknown asset, overlap, out-of-period, missing
  filler, bad ad break) as a list of messages

### `DELETE /channels/{channel_id}/schedule`
Clears the schedule; the channel reverts to looping the catalog.

### `POST /channels/{channel_id}/schedule/generate`
AI director — generate and apply a schedule from a brief. Requires
`ANTHROPIC_API_KEY`.
```json
{"brief": "90s action night, family-friendly until 21:00", "period_seconds": 10800}
```
- `200` → `{"status": "ok", "channel_id": "...", "filler_asset_id": "...",
  "period_seconds": ..., "entries": [...], "ad_breaks": [...]}`
- `503` → no API credentials · `422` → empty catalog / couldn't produce a valid
  schedule

---

## True-encode (optional)

Continuous branded FFmpeg playout, served under `/encoded/{channel_id}/playlist.m3u8`.

### `POST /channel/{channel_id}/encode/start`
- `200` → `{"status": "started", "playlist_url": "/encoded/demo/playlist.m3u8"}`
- `503` → no catalog · `409` → segment files not present locally

### `POST /channel/{channel_id}/encode/stop`
- `200` → `{"status": "stopped" | "not_running"}`

### `GET /channel/{channel_id}/encode/status`
- `200` → `{"running": true, "playlist_url": "/encoded/demo/playlist.m3u8"}`

---

## Utility

| Endpoint | Description |
|---|---|
| `GET /` | Redirects to the demo player (`/demo/`) |
| `GET /health` | `{"status": "ok"}` |
| `GET /segments/{asset}/{n}.ts` | Segment delivery (nginx/CDN in prod) |
| `GET /demo/` | hls.js demo player with on-air bar + guide |

Interactive docs (Swagger UI) are at `GET /docs`, served by FastAPI.
