"""LLM mode helpers for local harness runs."""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal

LLMMode = Literal["real", "fake", "recorded"]


@dataclass
class HarnessLLMConfig:
    mode: LLMMode = "fake"
    fake_responses: list[str] = field(default_factory=list)
    recording_path: str | None = None


class FakeLLM:
    def __init__(self, fake_responses: list[str] | None = None):
        self.fake_responses = list(fake_responses or [])

    def next_response(self) -> str:
        if not self.fake_responses:
            raise AssertionError("FakeLLM response queue exhausted")
        return self.fake_responses.pop(0)


def resolve_llm_mode(mode: str | None = None) -> LLMMode:
    resolved = mode or os.getenv("SARA_HARNESS_LLM_MODE", "fake")
    if resolved not in {"real", "fake", "recorded"}:
        raise ValueError(f"Invalid LLM mode: {resolved}")
    return resolved  # type: ignore[return-value]


@dataclass
class _FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class _FakeResponse:
    text: str
    stop_reason: str = "end_turn"

    @property
    def content(self):
        return [_FakeTextBlock(self.text)]


@contextmanager
def install_fake_anthropic(fake: FakeLLM):
    """Patch Anthropic calls inside `sara_agent` for deterministic harness runs."""
    from app.agent import sara_agent

    original_create = sara_agent.anthropic_client.messages.create

    def fake_create(*args, **kwargs):
        return _FakeResponse(fake.next_response())

    sara_agent.anthropic_client.messages.create = fake_create
    try:
        yield
    finally:
        sara_agent.anthropic_client.messages.create = original_create

