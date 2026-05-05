"""Fake Telegram transport for local harness and tests."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class TelegramCapture:
    messages: list[str] = field(default_factory=list)
    keyboards: list[dict] = field(default_factory=list)
    edits: list[dict] = field(default_factory=list)
    callbacks: list[dict] = field(default_factory=list)


def _keyboard_rows(reply_markup) -> list[list[str]]:
    if not reply_markup:
        return []
    rows = getattr(reply_markup, "inline_keyboard", None) or getattr(reply_markup, "keyboard", None)
    if not rows:
        return []
    return [[getattr(button, "text", str(button)) for button in row] for row in rows]


def install_fake_telegram(
    capture: TelegramCapture | None = None,
    *,
    echo: bool = False,
) -> TelegramCapture:
    """Patch Telegram Bot API methods and capture outbound traffic locally."""
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
    import app.services.telegram as _tg

    capture = capture or TelegramCapture()

    async def fake_send(*args, **kwargs):
        text = kwargs.get("text", args[1] if len(args) > 1 else "")
        reply_markup = kwargs.get("reply_markup")
        rows = _keyboard_rows(reply_markup)
        capture.messages.append(text)
        if rows:
            capture.keyboards.append({"text": text, "rows": rows, "markup": reply_markup})
        if echo:
            print(f"\n[Sara]: {text}")
            for row in rows:
                print("  " + "  ".join(f"[{label}]" for label in row))
            print()

        class FakeMsg:
            message_id = 999

        return FakeMsg()

    async def fake_edit(*args, **kwargs):
        reply_markup = kwargs.get("reply_markup")
        rows = _keyboard_rows(reply_markup)
        capture.edits.append({"kwargs": kwargs, "rows": rows})
        if echo and rows:
            print("[Teclado atualizado]:")
            for row in rows:
                print("  " + "  ".join(f"[{label}]" for label in row))
            print()

    async def fake_answer(*args, **kwargs):
        capture.callbacks.append({"args": args, "kwargs": kwargs})

    type(_tg.bot).send_message = fake_send
    type(_tg.bot).edit_message_reply_markup = fake_edit
    type(_tg.bot).edit_message_text = fake_edit
    type(_tg.bot).answer_callback_query = fake_answer
    return capture

