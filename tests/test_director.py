"""AI director tool-use loop, driven by a scripted fake client (no API key)."""

import json
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


class OpenAIFakeCompletions:
    def __init__(self, script):
        self._script = list(script)
        self.calls = 0
        self.last_kwargs = None

    def create(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        return self._script.pop(0)


class OpenAIFakeClient:
    def __init__(self, script):
        self.chat = SimpleNamespace(completions=OpenAIFakeCompletions(script))


def oa_call(call_id, name, arguments):
    return SimpleNamespace(
        id=call_id, function=SimpleNamespace(name=name, arguments=arguments)
    )


def oa_response(tool_calls=None, content=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


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


# --- OpenAI-compatible providers (google, meta) ----------------------------


@pytest.mark.parametrize("provider", ["google", "meta"])
def test_openai_compat_happy_path(session, provider):
    client = OpenAIFakeClient(
        [
            oa_response([oa_call("c1", "list_assets", "{}")]),
            oa_response([oa_call("c2", "submit_schedule", json.dumps(VALID_SUBMIT))]),
        ]
    )
    result = director.build_schedule("fill the hour", 60, session, client, provider=provider)
    assert result["entries"] == [{"asset_id": "movie", "start_offset": 0.0}]
    assert client.chat.completions.calls == 2


def test_openai_compat_sends_tools_and_model(session):
    client = OpenAIFakeClient(
        [oa_response([oa_call("c1", "submit_schedule", json.dumps(VALID_SUBMIT))])]
    )
    director.build_schedule(
        "x", 60, session, client, provider="google", model="gemini-custom"
    )
    kwargs = client.chat.completions.last_kwargs
    assert kwargs["model"] == "gemini-custom"
    assert {t["function"]["name"] for t in kwargs["tools"]} == {
        "list_assets",
        "submit_schedule",
    }
    assert kwargs["messages"][0]["role"] == "system"


def test_openai_compat_self_corrects_and_returns_tool_results(session):
    overlapping = {
        "filler_asset_id": "ident",
        "entries": [
            {"asset_id": "movie", "start_offset": 0},
            {"asset_id": "movie", "start_offset": 6},
        ],
    }
    client = OpenAIFakeClient(
        [
            oa_response([oa_call("c1", "submit_schedule", json.dumps(overlapping))]),
            oa_response([oa_call("c2", "submit_schedule", json.dumps(VALID_SUBMIT))]),
        ]
    )
    director.build_schedule("x", 60, session, client, provider="meta")
    assert client.chat.completions.calls == 2


def test_openai_compat_bad_json_arguments_are_reported_back(session):
    client = OpenAIFakeClient(
        [
            oa_response([oa_call("c1", "submit_schedule", "{not json")]),
            oa_response([oa_call("c2", "submit_schedule", json.dumps(VALID_SUBMIT))]),
        ]
    )
    result = director.build_schedule("x", 60, session, client, provider="meta")
    assert result["filler_asset_id"] == "ident"
    assert client.chat.completions.calls == 2


def test_openai_compat_missing_call_id_gets_one(session):
    client = OpenAIFakeClient(
        [
            oa_response([oa_call("", "list_assets", "{}")]),
            oa_response([oa_call("", "submit_schedule", json.dumps(VALID_SUBMIT))]),
        ]
    )
    director.build_schedule("x", 60, session, client, provider="google")
    tool_msgs = [m for m in client.chat.completions.last_kwargs["messages"] if m["role"] == "tool"]
    assert tool_msgs and all(m["tool_call_id"] for m in tool_msgs)


def test_openai_compat_stops_without_tool_calls(session):
    client = OpenAIFakeClient([oa_response(content="all done")])
    with pytest.raises(director.DirectorError):
        director.build_schedule("x", 60, session, client, provider="meta")


def test_unknown_provider_is_unavailable(session):
    with pytest.raises(director.DirectorUnavailable):
        director.build_schedule("x", 60, session, FakeClient([]), provider="nope")


def test_make_client_google_requires_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(director.DirectorUnavailable):
        director.make_client("google")


# --- provider API failures and model overrides -----------------------------


def _http_error(cls, status, message):
    import httpx

    request = httpx.Request("POST", "https://api.example.test/v1")
    return cls(message, response=httpx.Response(status, request=request), body=None)


class RaisingMessages:
    def __init__(self, exc):
        self._exc = exc

    def create(self, **kwargs):
        raise self._exc


def test_anthropic_api_error_becomes_upstream_error(session):
    import anthropic

    exc = _http_error(anthropic.BadRequestError, 400, "credit balance is too low")
    client = SimpleNamespace(messages=RaisingMessages(exc))
    with pytest.raises(director.DirectorUpstreamError) as info:
        director.build_schedule("x", 60, session, client)
    assert "anthropic provider request failed" in str(info.value)
    assert "HTTP 400" in str(info.value)
    assert "credit balance is too low" in str(info.value)


def test_openai_compat_api_error_becomes_upstream_error(session):
    openai = pytest.importorskip("openai")

    exc = _http_error(openai.NotFoundError, 404, "model is no longer available")
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=RaisingMessages(exc))
    )
    with pytest.raises(director.DirectorUpstreamError) as info:
        director.build_schedule("x", 60, session, client, provider="google")
    assert "google provider request failed" in str(info.value)
    assert "HTTP 404" in str(info.value)


def test_non_api_errors_are_not_swallowed(session):
    client = SimpleNamespace(messages=RaisingMessages(RuntimeError("bug")))
    with pytest.raises(RuntimeError):
        director.build_schedule("x", 60, session, client)


@pytest.mark.parametrize(
    "provider, env_var",
    [
        ("anthropic", "ANTHROPIC_MODEL"),
        ("google", "GEMINI_MODEL"),
        ("groq", "GROQ_MODEL"),
        ("meta", "LLAMA_MODEL"),
    ],
)
def test_default_model_can_be_overridden_by_env(monkeypatch, provider, env_var):
    from app.ai.providers import get_provider

    spec = get_provider(provider)
    monkeypatch.delenv(env_var, raising=False)
    assert spec.resolved_model() == spec.default_model
    monkeypatch.setenv(env_var, "custom-model")
    assert spec.resolved_model() == "custom-model"


def test_groq_requires_its_own_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(director.DirectorUnavailable, match="GROQ_API_KEY"):
        director.make_client("groq")


def test_groq_client_uses_groq_endpoint(monkeypatch):
    pytest.importorskip("openai")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    client = director.make_client("groq")
    assert str(client.base_url).startswith("https://api.groq.com/openai/v1")
