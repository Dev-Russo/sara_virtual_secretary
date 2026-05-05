"""Conversation simulation entry points for the development harness."""

from __future__ import annotations

from app.agent.session import get_session_context, get_session_state

from tests.harness.db import CORE_USER_ID
from tests.harness.llm import FakeLLM, install_fake_anthropic, resolve_llm_mode
from tests.harness.telegram import install_fake_telegram

DEFAULT_FAKE_RESPONSE = "[fake-llm] resposta de teste"


def simulate_message(
    text: str,
    *,
    user_id: str = CORE_USER_ID,
    llm_mode: str | None = None,
    fake_responses: list[str] | None = None,
) -> dict:
    capture = install_fake_telegram()
    mode = resolve_llm_mode(llm_mode)

    from app.agent.sara_agent import chat

    if mode == "recorded":
        raise NotImplementedError("recorded LLM mode is reserved for Phase 2")

    if mode == "fake":
        fake = FakeLLM(fake_responses or [DEFAULT_FAKE_RESPONSE])
        with install_fake_anthropic(fake):
            response = chat(text, user_id=user_id)
    else:
        response = chat(text, user_id=user_id)

    return {
        "user_id": user_id,
        "input": text,
        "response": response,
        "messages": capture.messages,
        "keyboards": capture.keyboards,
        "session_state": get_session_state(user_id),
        "session_context": get_session_context(user_id),
        "llm_mode": mode,
    }

