# Showrunner: Build Plan

> An AI-programmed linear TV channel: a cloud playout engine that turns a media
> library into a 24/7 HLS stream, with Claude as the programming director.

## 1. The pitch

FAST channels (Pluto TV, Samsung TV Plus, Tubi Linear) are the fastest-growing
segment of broadcast. Every one of them needs two things: a playout engine that
assembles assets into a continuous live stream, and a human programmer who
decides what airs when. Showrunner builds the first and replaces the second
with an LLM: *"program Saturday evening as a 90s action night, family-friendly
until 21:00"* becomes a valid, gapless schedule.

## 2. Core architecture decision: manifest stitching, not re-encoding

Two ways to build playout:

| Approach | How it works | Verdict |
|---|---|---|
| **True playout** (Cinegy, Grass Valley) | One encoder runs 24/7, switching inputs live | Frame-accurate, supports live graphics, and brutally hard (clock discipline, gapless input switching, encoder crashes). Deferred to Phase 7+. |
| **Manifest stitching** (how most FAST channels actually run) | Assets are pre-transcoded to one uniform ladder and pre-cut into HLS segments at ingest. The "channel" is a stateless web service that maps wall clock + schedule → a sliding-window live manifest over pre-cut segments. | **No video processing at playout time at all.** This is what we build. |

**The stateless property is the architectural centerpiece:** every manifest is a
pure function of `(epoch, catalog, schedule, wall clock)`. Nothing ticks.
Replicas agree byte-for-byte, restarts can't drift the channel, and horizontal
scaling is free.

## 3. System components

```
 Ingest & normalize ──────────────▶ Segment store
 (FFmpeg, uniform ladder,           (pre-cut HLS chunks,
  Celery worker)                     MinIO/S3, nginx-served)
        │                                   │
        ▼                                   ▼
 Schedule API ──▶ Timeline resolver ──▶ Manifest generator ──▶ Player
 (FastAPI +       (wall clock →         (stateless HLS          (hls.js / VLC,
  Postgres)        asset+offset)         window, 1s cache)       pulls manifest
        │                                                        + chunks)
        ▼
 EPG export (XMLTV)          AI director (Claude tool-use, Phase 6)
```

- **Ingest & normalize**: FFmpeg transcodes every asset to one identical spec
  (codec/resolution/fps/GOP/audio) and pre-cuts 4s segments. This one-time
  normalization is what makes gapless joins possible. Celery worker; exact
  durations recorded per segment.
- **Schedule API**: CRUD for the programme timeline plus policy (filler for
  gaps, join-point semantics for mid-air edits).
- **Timeline resolver**: pure function `(wall clock, schedule) → asset +
  offset`. Fully unit-testable; every drift bug lives or dies here.
- **Manifest generator**: computes the live window (last N segments), emits
  `EXT-X-DISCONTINUITY` at joins and `EXT-X-PROGRAM-DATE-TIME` for clock
  alignment. Stateless; cached 1s.
- **Outputs**: XMLTV EPG from the same schedule; `EXT-X-CUE-OUT/IN` ad
  markers later.
- **AI director**: Claude with tool access to the catalog and schedule API
  (see §7).

## 4. Tech stack (performance-conscious Python core)

| Layer | Choice | Why |
|---|---|---|
| API framework | FastAPI + uvicorn (uvloop) | Team-of-one velocity; 15-25k req/s is far beyond demo needs |
| Ingest jobs | Celery + Redis | Standard, matches existing skills |
| Schedule store | Postgres | Relational fits schedules; SQLite acceptable for dev |
| Segment store | MinIO (S3-compatible) locally, S3 in cloud | Segments never transit app code |
| Static delivery | nginx (→ CDN at scale) | 99.9% of bytes bypass Python entirely |
| Manifest cache | In-process/Redis, **1s TTL** | One computation serves all concurrent viewers: the single biggest performance lever |
| Media tooling | FFmpeg / ffprobe | Industry standard |
| AI | Claude API: `claude-opus-4-8` (director), `claude-haiku-4-5` (bulk tagging later) | Reasoning-heavy schedule generation |
| Packaging | Docker Compose, GitHub Actions CI | One-command startup, tests on every push |

**Performance strategy (in order of impact):**
1. Segments served by nginx/CDN, never by app code.
2. 1-second manifest cache: collapses per-viewer load to ~1 computation/sec.
3. uvloop + orjson + multiple uvicorn workers (or granian): free multipliers.
4. **Phase 7:** rewrite the manifest generator (~300 lines, existing test suite
   to validate against) in Go as a standalone origin service; publish a
   `wrk` before/after benchmark in the README. Python origin stays as the
   reference implementation.

## 5. Phased roadmap

Each phase ends with something that visibly works.

- [ ] **Phase 0: Fake-live spike** *(~1 weekend)*
  Loop pre-segmented test assets (FFmpeg `testsrc`) behind a sliding-window
  manifest. **Done when:** VLC and hls.js play the "channel". *(scaffolded)*
- [x] **Phase 1: Hardening the core** *(~1 weekend)*, **done**
  Resolver edge cases (single-asset loop, window > catalog, first-segment
  discontinuity, ~1-year uptime, the discontinuity-accounting invariant,
  no-gap continuity) plus FastAPI integration tests including proof the
  manifest cache serves stale within TTL. 26 tests, CI green;
  media/discontinuity sequences provably monotonic.
- [x] **Phase 2: Ingest pipeline** *(~1-2 weekends)*, **done**
  Upload endpoint → FFmpeg normalize (uniform ladder) → segment → storage →
  exact ffprobe'd durations + segment lists into the catalog. SQLAlchemy
  catalog (SQLite in dev, Postgres in prod), pluggable segment store
  (local disk or S3/MinIO), and a task queue that runs eagerly in-process
  with no broker or on a Celery worker when `SHOWRUNNER_BROKER` is set.
  36 tests including a real-FFmpeg check that three different source files
  become interchangeable segment sets (identical codec/resolution/fps).
- [x] **Phase 3: Real scheduler** *(~1-2 weekends)*, **done**
  `ScheduledTimeline` resolves programmes at target offsets with a looping
  filler tiling every gap, flattened into one cycle and resolved against the
  wall clock (same drift-free/stateless engine, same `Window` contract as the
  looping timeline). Schedule CRUD (`GET/PUT/DELETE /schedule`) with a shared
  validator (rejects overlaps, out-of-period entries, missing filler); edits
  rebuild the timeline immediately and only affect future segments. 53 tests
  including a brute-force oracle that verifies every discontinuity flag and
  the discontinuity-sequence segment-by-segment across cycles.
  **Timing note:** filler is laid as whole segments, so a programme starts
  within one segment (~4s) of its target (segment-accurate, not
  frame-accurate), which is what keeps the cycle drift-free.
- [x] **Phase 4: 24-hour soak** *(~2 weekends, the hard one)*, **done**
  Two-part verification. A deterministic simulation (`tests/test_soak.py`)
  fast-forwards a full 24h through the resolver and proves every invariant on
  every one of the ~21.6k segments: no gaps, monotonic sequences, exact
  program-date-time continuity across discontinuities, and a live edge that
  always contains the wall clock (zero drift): a proof, not a sample. A live
  harness (`scripts/soak.py`) polls a running server over real time, measures
  actual drift and stalls, checks every referenced segment is fetchable, and
  optionally decodes the stream with ffmpeg; runs for any duration up to the
  full day. 56 tests. Verified: 24h simulation clean; a live 40s run showed
  zero stalls, sub-segment drift, and successful ffmpeg decode.
- [x] **Phase 5: Broadcast polish** *(~1-2 weekends)*, **done**
  XMLTV EPG (`/epg.xml`) plus a JSON view (`/epg.json`) that project the
  cyclic schedule onto absolute time, filler labelled as such.
  `EXT-X-CUE-OUT/IN` ad-break markers driven by ad avails on the schedule
  (`ad_breaks` on `PUT /schedule`), recurring each cycle: the hook for
  SCTE-35/SSAI. Upgraded demo UI: live on-air bar with progress, up-next, and
  a rolling programme guide. 68 tests. *(README demo GIF still to capture.)*
- [x] **Phase 6: AI programming director** *(~1-2 weekends)*, **done**
  `POST /schedule/generate` runs a Claude (`claude-opus-4-8`, adaptive
  thinking) tool-use loop: `list_assets` → `submit_schedule`, with the shared
  `validate_schedule` as the safety rail: the LLM proposes, deterministic
  validation disposes, and Claude iterates on any violations it's handed back.
  Accepted schedules persist through the same path as `PUT /schedule`. Asset
  metadata (`genre`/`rating`/`year`, set via `PATCH /assets/{id}`) lets briefs
  like "family-friendly until 21:00" be grounded. The client is injected, so
  the whole loop, including self-correction, is covered in CI by a scripted
  fake with no API key (live use needs `ANTHROPIC_API_KEY`). 77 tests.
- [x] **Phase 7: Go manifest origin + benchmark**, **done**
  Control-plane / data-plane split: Python compiles the schedule into a cycle
  snapshot (`app/core/snapshot.py`); a standalone Go service (`go-origin/`)
  serves manifests from it statelessly, re-implementing only the hot path
  (`render_media_playlist`), validated byte-for-byte against the canonical
  Python renderer by 126 golden cases generated from it. Measured with `wrk`:
  **~8.9k req/s (Python/uvicorn) → ~147k req/s (Go), ~16× throughput**,
  p50 latency 7.6ms → 0.5ms. CI runs both the Python suite and `go test`.
  81 Python tests + the Go golden test.

README demo GIF captured from the live stream (`docs/demo.gif`).

- [x] **Multi-channel**: one asset library, many independent channels (own
  name/epoch/schedule/ad-breaks). Parametric `/channel/{id}/...` routes with
  the single-channel routes kept as `demo` aliases; `GET/POST /channels`,
  `DELETE /channels/{id}`; per-channel timeline/manifest caches; the Go origin
  serves multiple channels from a snapshot directory.
- [x] **True-encode mode**: an optional continuous-FFmpeg playout that
  re-encodes a channel into one stream with burned-in branding (a translucent
  lower-third + channel name), served under `/encoded/{id}/playlist.m3u8`.
  `POST /channel/{id}/encode/{start,stop}`, `GET .../encode/status`; degrades
  to bar-only branding when the local ffmpeg lacks `drawtext`.

99 Python tests + Go golden/routing tests. No stretch ideas remaining.

## 6. Hard problems & mitigations

| Problem | Mitigation |
|---|---|
| **Drift**: segment durations don't sum to schedule time | Schedule positions derive from *actual* ffprobe'd durations, never nominal; resolver recomputes from wall clock per request, so error cannot accumulate |
| **Joins between assets** break players | Ingest-time normalization to one spec + `EXT-X-DISCONTINUITY` at every join |
| **Mid-air schedule edits** | Served window is immutable; edits apply from the next join point |
| **The 3am problem** (midnight, DST, empty schedule) | UTC everywhere; filler policy as universal fallback; explicit tests per case |
| **Thundering-herd manifests** | 1s cache; `Cache-Control: max-age=1` so CDNs collapse it further |

## 7. AI programming director (Phase 6 design)

- **Model:** `claude-opus-4-8` (schedule reasoning), adaptive thinking.
- **Tools (strict schemas):**
  - `list_assets()` → catalog with durations, genres, ratings, tags
  - `get_schedule(day)` → current entries
  - `submit_schedule(entries)` → validated server-side: no overlaps, no gaps
    beyond filler policy, rating windows respected (e.g. family-friendly
    before 21:00); validation errors returned to Claude for self-correction
- **Flow:** brief → Claude inspects catalog → drafts schedule → submits →
  server validates → Claude fixes violations → committed schedule + generated
  EPG synopses.
- **Safety rail:** the engine only ever airs what `submit_schedule` accepted,
  the LLM proposes, deterministic validation disposes.

## 8. MVP definition of done

One channel · 3+ assets + filler · watchable in VLC and hls.js · 24h+ without
drift or stalls · EPG output · AI director generates a valid evening schedule
from a one-sentence brief · README with architecture diagram, demo GIF, and
one-command `docker compose up`.

## 9. Repo layout

```
showrunner/
├── PLAN.md                  ← this file
├── README.md
├── docker-compose.yml       # api now; postgres/redis/minio via --profile full
├── Dockerfile
├── app/
│   ├── main.py              # FastAPI wiring
│   ├── config.py            # env-driven settings
│   ├── core/
│   │   ├── timeline.py      # resolver: wall clock → window (pure)
│   │   ├── manifest.py      # window → m3u8 (pure)
│   │   └── catalog.py       # catalog.json loader
│   ├── routers/channel.py   # /channel/demo/playlist.m3u8, /now
│   ├── ingest/              # Phase 2: Celery + FFmpeg normalize
│   └── ai/director.py       # Phase 6: Claude tool-use agent
├── scripts/make_demo_assets.py  # FFmpeg testsrc → segments + catalog
├── demo/player.html         # hls.js demo player
└── tests/                   # pytest: timeline + manifest
```
