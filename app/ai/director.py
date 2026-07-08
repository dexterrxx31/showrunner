"""AI programming director — Phase 6 (experimental skeleton).

Turns a natural-language programming brief into a channel schedule.
Claude inspects the catalog through tool calls and submits a schedule;
the server-side validator is the safety rail — the engine only ever airs
what `submit_schedule` accepted.

Requires: pip install anthropic, and ANTHROPIC_API_KEY in the environment.
"""

from __future__ import annotations

import json
from typing import Any

MODEL = "claude-opus-4-8"

SYSTEM = """You are the programming director for a linear TV channel.
Given a programming brief, inspect the asset catalog and produce a schedule.
Rules: no overlapping entries, fill the whole requested span (the engine adds
filler for small gaps), respect content-rating windows in the brief, and vary
the pacing. Always finish by calling submit_schedule."""

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_assets",
        "description": "List every asset in the catalog with id, title, "
        "duration in seconds, and tags. Call this before drafting a schedule.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "submit_schedule",
        "description": "Submit the final schedule. Entries must be sorted by "
        "start time and non-overlapping. Returns validation errors to fix, "
        "or ok=true.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "entries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "asset_id": {"type": "string"},
                            "start_utc": {
                                "type": "string",
                                "format": "date-time",
                            },
                        },
                        "required": ["asset_id", "start_utc"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["entries"],
            "additionalProperties": False,
        },
    },
]


def build_schedule(brief: str, catalog: list[dict], validator) -> list[dict]:
    """Run the tool-use loop until Claude submits a schedule that validates.

    `validator(entries) -> list[str]` returns human-readable violations
    (empty list means the schedule is accepted).
    """
    import anthropic  # optional dependency until Phase 6

    client = anthropic.Anthropic()
    messages: list[dict] = [{"role": "user", "content": brief}]

    for _ in range(10):
        response = client.messages.create(
            model=MODEL,
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
        accepted: list[dict] | None = None
        for block in response.content:
            if block.type != "tool_use":
                continue
            if block.name == "list_assets":
                content = json.dumps(catalog)
            elif block.name == "submit_schedule":
                entries = block.input["entries"]
                violations = validator(entries)
                if violations:
                    content = json.dumps({"ok": False, "violations": violations})
                else:
                    content = json.dumps({"ok": True})
                    accepted = entries
            else:
                content = f"unknown tool {block.name}"
            results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": content}
            )
        messages.append({"role": "user", "content": results})
        if accepted is not None:
            return accepted

    raise RuntimeError("director did not produce a valid schedule")
