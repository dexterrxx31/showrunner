"""LLM provider adapters for the AI director.

The director's tool-use loop is provider-neutral: each adapter owns its own
message history and translates to and from its SDK's wire format. A turn
returns the tool calls the model made; an empty list means the model stopped.

Anthropic uses its native Messages API. Google (Gemini), Groq, and Meta (Llama)
are reached through OpenAI-compatible chat completions endpoints: Gemini via
Google's OpenAI compatibility layer, Groq via its OpenAI-compatible API, Llama
via any host that serves it that way (Ollama, Together, Meta's Llama API).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import anthropic


class ProviderUnavailable(Exception):
    """The provider can't be used (missing credentials or SDK)."""


class ProviderError(Exception):
    """The provider's API rejected or failed a request (billing, auth, model, outage)."""


_MAX_ERROR_CHARS = 300


def _describe(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    message = str(getattr(exc, "message", None) or exc)[:_MAX_ERROR_CHARS]
    return f"HTTP {status}: {message}" if status else message


def _openai_api_errors() -> tuple[type[Exception], ...]:
    # The openai SDK is optional; without it no client exists to raise these.
    try:
        import openai
    except ImportError:
        return ()
    return (openai.APIError,)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict | None  # None when the model sent arguments that weren't valid JSON


class AnthropicConversation:
    def __init__(self, client, model, system, tools, user_text):
        self._client = client
        self._model = model
        self._system = system
        self._tools = tools
        self._messages = [{"role": "user", "content": user_text}]

    def step(self) -> list[ToolCall]:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=16000,
                thinking={"type": "adaptive"},
                system=self._system,
                tools=self._tools,
                messages=self._messages,
            )
        except anthropic.APIError as e:
            raise ProviderError(_describe(e)) from e
        if response.stop_reason != "tool_use":
            return []
        self._messages.append({"role": "assistant", "content": response.content})
        return [
            ToolCall(id=b.id, name=b.name, input=b.input)
            for b in response.content
            if getattr(b, "type", None) == "tool_use"
        ]

    def reply(self, results: list[tuple[str, str]]) -> None:
        self._messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": call_id, "content": content}
                    for call_id, content in results
                ],
            }
        )


class OpenAICompatConversation:
    def __init__(self, client, model, system, tools, user_text):
        self._client = client
        self._model = model
        self._tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]
        self._messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ]

    def step(self) -> list[ToolCall]:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                tools=self._tools,
                messages=self._messages,
            )
        except _openai_api_errors() as e:
            raise ProviderError(_describe(e)) from e
        message = response.choices[0].message
        raw_calls = getattr(message, "tool_calls", None) or []
        if not raw_calls:
            return []

        calls = []
        for n, raw in enumerate(raw_calls):
            # Some hosts return an empty id; the result still has to reference one.
            call_id = raw.id or f"call_{len(self._messages)}_{n}"
            try:
                parsed = json.loads(raw.function.arguments or "{}")
            except json.JSONDecodeError:
                parsed = None
            calls.append(ToolCall(id=call_id, name=raw.function.name, input=parsed))

        self._messages.append(
            {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {
                            "name": raw.function.name,
                            "arguments": raw.function.arguments or "{}",
                        },
                    }
                    for c, raw in zip(calls, raw_calls)
                ],
            }
        )
        return calls

    def reply(self, results: list[tuple[str, str]]) -> None:
        for call_id, content in results:
            self._messages.append(
                {"role": "tool", "tool_call_id": call_id, "content": content}
            )


@dataclass(frozen=True)
class Provider:
    name: str
    default_model: str
    conversation: type
    make_client: callable
    model_env: str  # env var that overrides default_model

    def resolved_model(self) -> str:
        return os.environ.get(self.model_env) or self.default_model


def _anthropic_client():
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise ProviderUnavailable("set ANTHROPIC_API_KEY to use the anthropic provider")
    import anthropic

    return anthropic.Anthropic()


def _openai_client(*, base_url: str, api_key: str):
    try:
        import openai
    except ImportError as e:
        raise ProviderUnavailable("pip install openai to use this provider") from e
    return openai.OpenAI(base_url=base_url, api_key=api_key)


def _google_client():
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise ProviderUnavailable("set GEMINI_API_KEY to use the google provider")
    return _openai_client(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key=key,
    )


def _groq_client():
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise ProviderUnavailable("set GROQ_API_KEY to use the groq provider")
    return _openai_client(base_url="https://api.groq.com/openai/v1", api_key=key)


def _meta_client():
    # Defaults to a local Ollama, which ignores the key but the SDK requires one.
    return _openai_client(
        base_url=os.environ.get("LLAMA_BASE_URL", "http://localhost:11434/v1"),
        api_key=os.environ.get("LLAMA_API_KEY", "ollama"),
    )


PROVIDERS: dict[str, Provider] = {
    "anthropic": Provider(
        "anthropic", "claude-opus-4-8", AnthropicConversation, _anthropic_client,
        "ANTHROPIC_MODEL",
    ),
    "google": Provider(
        "google", "gemini-3.1-pro-preview", OpenAICompatConversation, _google_client,
        "GEMINI_MODEL",
    ),
    "groq": Provider(
        "groq", "openai/gpt-oss-120b", OpenAICompatConversation, _groq_client,
        "GROQ_MODEL",
    ),
    "meta": Provider(
        "meta", "llama3.3", OpenAICompatConversation, _meta_client, "LLAMA_MODEL"
    ),
}


def get_provider(name: str) -> Provider:
    try:
        return PROVIDERS[name]
    except KeyError:
        raise ProviderUnavailable(
            f"unknown provider '{name}' (choose from {', '.join(PROVIDERS)})"
        ) from None
