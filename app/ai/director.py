"""AI programming director — turn a natural-language brief into a schedule.

Claude inspects the catalog through tool calls and submits a schedule; the
server-side `validate_schedule` is the safety rail. The LLM proposes, the
deterministic validator disposes — the engine only ever airs a schedule that
passed validation, and Claude iterates on any violations it's handed back.

The client is injected so the whole tool-use loop (including validation and
self-correction) is testable with a scripted fake — no API key needed in CI.
Live use needs ANTHROPIC_API_KEY; `make_client()` enforces that.
"""

from __future__ import annotations

import json

from app.core.schedule import validate_schedule
from app.core.timeline import Asset, Segment

MODEL = "claude-opus-4-8"
MAX_ITERATIONS = 12

SYSTEM = """You are the programming director for a linear TV channel.

Given a brief, design a schedule that fills the channel's repeating cycle.
First call list_assets to see the available programmes (id, title, duration in
seconds, and any genre/rating/year metadata). Then call submit_schedule with:
  - filler_asset_id: an asset looped to cover every gap (idents, trailers)
  - entries: programmes to air, each an asset_id and a start_offset (seconds
    into the cycle)
  - ad_breaks (optional): ad avails, each a start_offset and duration

Rules:
  - Entries must not overlap and must fit within the cycle period.
  - Use the metadata to honour the brief — e.g. keep higher-rated titles after
    a watershed, group by genre or era when asked.
  - Timing is segment-accurate (~4s); exact-to-the-second offsets aren't needed.
  - If submit_schedule returns violations, fix them and submit again.
Finish only once submit_schedule reports the schedule was accepted."""

TOOLS = [
    {
        "name": "list_assets",
        "description": "List available programmes with durations and metadata, "
        "plus the channel cycle period. Call this before designing a schedule.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "submit_schedule",
        "description": "Submit a schedule for validation. Returns {ok:true} if "
        "accepted, or {ok:false, violations:[...]} to fix and resubmit.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filler_asset_id": {"type": "string"},
                "entries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "asset_id": {"type": "string"},
                            "start_offset": {"type": "number"},
                        },
                        "required": ["asset_id", "start_offset"],
                    },
                },
                "ad_breaks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "start_offset": {"type": "number"},
                            "duration": {"type": "number"},
                        },
                        "required": ["start_offset", "duration"],
                    },
                },
            },
            "required": ["filler_asset_id", "entries"],
        },
    },
]


class DirectorUnavailable(Exception):
    """The director can't run (e.g. no API credentials)."""


class DirectorError(Exception):
    """The director ran but couldn't produce a valid schedule."""


def make_client():
    import os

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise DirectorUnavailable(
            "set ANTHROPIC_API_KEY to use the AI programming director"
        )
    import anthropic

    return anthropic.Anthropic()


def _catalog(session):
    """Return (summary_for_the_model, {id: Asset} for validation)."""
    from app.models import AssetRow

    summary = []
    assets_by_id: dict[str, Asset] = {}
    for r in session.query(AssetRow).order_by(AssetRow.position, AssetRow.created_at):
        segments = tuple(Segment(uri=s.uri, duration=s.duration) for s in r.segments)
        if not segments:
            continue
        assets_by_id[r.id] = Asset(id=r.id, title=r.title, segments=segments)
        summary.append(
            {
                "asset_id": r.id,
                "title": r.title,
                "duration_seconds": round(sum(s.duration for s in segments), 1),
                "genre": r.genre,
                "rating": r.rating,
                "year": r.year,
            }
        )
    return summary, assets_by_id


def _evaluate(inp: dict, period: float, assets_by_id: dict[str, Asset]):
    """Validate a submitted schedule. Returns (accepted_dict | None, violations)."""
    filler_id = inp.get("filler_asset_id")
    entries_in = inp.get("entries") or []
    ad_in = inp.get("ad_breaks") or []

    violations: list[str] = []
    if filler_id not in assets_by_id:
        violations.append(f"unknown filler asset '{filler_id}'")
    resolved = []
    for e in entries_in:
        aid = e.get("asset_id")
        if aid not in assets_by_id:
            violations.append(f"unknown asset '{aid}'")
            continue
        resolved.append((assets_by_id[aid], float(e.get("start_offset", 0))))
    ad_breaks = [(float(a["start_offset"]), float(a["duration"])) for a in ad_in]

    if not violations:
        violations = validate_schedule(
            resolved, assets_by_id.get(filler_id), period, ad_breaks
        )
    if violations:
        return None, violations

    accepted = {
        "filler_asset_id": filler_id,
        "period_seconds": period,
        "entries": [
            {"asset_id": e["asset_id"], "start_offset": float(e["start_offset"])}
            for e in entries_in
        ],
        "ad_breaks": [
            {"start_offset": float(a["start_offset"]), "duration": float(a["duration"])}
            for a in ad_in
        ],
    }
    return accepted, []


def build_schedule(brief: str, period_seconds: float, session, client, *, model=MODEL) -> dict:
    summary, assets_by_id = _catalog(session)
    if not summary:
        raise DirectorError("catalog is empty — ingest assets before generating a schedule")

    messages = [
        {
            "role": "user",
            "content": (
                f"Programming brief: {brief}\n\n"
                f"The channel cycle period is {period_seconds:.0f} seconds. "
                f"Schedule programmes within [0, {period_seconds:.0f}] seconds."
            ),
        }
    ]

    for _ in range(MAX_ITERATIONS):
        response = client.messages.create(
            model=model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=SYSTEM,
            tools=TOOLS,
            messages=messages,
        )
        if response.stop_reason != "tool_use":
            break

        messages.append({"role": "assistant", "content": response.content})
        results = []
        accepted = None
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            if block.name == "list_assets":
                content = json.dumps({"period_seconds": period_seconds, "assets": summary})
            elif block.name == "submit_schedule":
                accepted, violations = _evaluate(block.input, period_seconds, assets_by_id)
                content = json.dumps(
                    {"ok": True} if accepted else {"ok": False, "violations": violations}
                )
            else:
                content = json.dumps({"error": f"unknown tool {block.name}"})
            results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": content}
            )
        messages.append({"role": "user", "content": results})

        if accepted is not None:
            return accepted

    raise DirectorError("the director did not produce a valid schedule")
