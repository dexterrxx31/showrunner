"""AI director tool-use loop, driven by a scripted fake client (no API key)."""

from types import SimpleNamespace

import pytest

from app import config
from app.ai import director


# --- fake Anthropic client ----------------------------------------------


def tool_use(block_id, name, tool_input):
    return SimpleNamespace(type="tool_use", id=block_id, name=name, input=tool_input)


def response(stop_reason, content):
    return SimpleNamespace(stop_reason=stop_reason, content=content)


class FakeMessages:
    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return self._script.pop(0)


class FakeClient:
    def __init__(self, script):
        self.messages = FakeMessages(script)


# --- fixture --------------------------------------------------------------


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path/'t.db'}")
    import app.db as db_module

    db_module.reset_engine()
    db_module.init_db()
    from app.models import AssetRow, SegmentRow

    with db_module.session_scope() as s:
        for pos, (aid, n) in enumerate([("movie", 3), ("ident", 1)]):
            row = AssetRow(id=aid, title=aid.title(), position=pos)
            for i in range(n):
                row.segments.append(
                    SegmentRow(idx=i, uri=f"/segments/{aid}/{i:05d}.ts", duration=4.0)
                )
            s.add(row)
    with db_module.session_scope() as s:
        yield s
    db_module.reset_engine()


VALID_SUBMIT = {
    "filler_asset_id": "ident",
    "entries": [{"asset_id": "movie", "start_offset": 0}],
    "ad_breaks": [],
}


def test_happy_path(session):
    client = FakeClient(
        [
            response("tool_use", [tool_use("t1", "list_assets", {})]),
            response("tool_use", [tool_use("t2", "submit_schedule", VALID_SUBMIT)]),
        ]
    )
    result = director.build_schedule("fill the hour", 60, session, client)
    assert result["filler_asset_id"] == "ident"
    assert result["entries"] == [{"asset_id": "movie", "start_offset": 0.0}]
    assert result["period_seconds"] == 60
    assert client.messages.calls == 2


def test_self_corrects_after_violation(session):
    overlapping = {
        "filler_asset_id": "ident",
        "entries": [
            {"asset_id": "movie", "start_offset": 0},
            {"asset_id": "movie", "start_offset": 6},  # 0-12 overlaps 6-18
        ],
    }
    client = FakeClient(
        [
            response("tool_use", [tool_use("t1", "list_assets", {})]),
            response("tool_use", [tool_use("t2", "submit_schedule", overlapping)]),
            response("tool_use", [tool_use("t3", "submit_schedule", VALID_SUBMIT)]),
        ]
    )
    result = director.build_schedule("fill the hour", 60, session, client)
    assert result["entries"] == [{"asset_id": "movie", "start_offset": 0.0}]
    assert client.messages.calls == 3  # it saw the violation and retried


def test_empty_catalog_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{tmp_path/'empty.db'}")
    import app.db as db_module

    db_module.reset_engine()
    db_module.init_db()
    with db_module.session_scope() as s:
        with pytest.raises(director.DirectorError):
            director.build_schedule("anything", 60, s, FakeClient([]))
    db_module.reset_engine()


def test_gives_up_after_max_iterations(session):
    bad = {"filler_asset_id": "ghost", "entries": []}  # filler never resolves
    script = [
        response("tool_use", [tool_use(f"t{i}", "submit_schedule", bad)])
        for i in range(director.MAX_ITERATIONS)
    ]
    with pytest.raises(director.DirectorError):
        director.build_schedule("x", 60, session, FakeClient(script))


def test_make_client_requires_credentials(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(director.DirectorUnavailable):
        director.make_client()
