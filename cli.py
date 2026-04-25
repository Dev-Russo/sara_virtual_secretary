import asyncio

# Monkey-patch Telegram bot BEFORE importing anything that calls it.
# Intercepts send_message / edit / answer so CLI never hits the real API.
import app.services.telegram as _tg


async def _mock_send(*args, **kwargs):
    text = kwargs.get("text", args[1] if len(args) > 1 else "")
    reply_markup = kwargs.get("reply_markup", None)
    if reply_markup and hasattr(reply_markup, "inline_keyboard"):
        print(f"\n[Sara]: {text}")
        for row in reply_markup.inline_keyboard:
            print("  " + "  ".join(f"[{btn.text}]" for btn in row))
    else:
        print(f"\n[Sara]: {text}")
    print()

    class _FakeMsg:
        message_id = 0

    return _FakeMsg()


async def _mock_edit(*args, **kwargs):
    reply_markup = kwargs.get("reply_markup", None)
    if reply_markup and hasattr(reply_markup, "inline_keyboard"):
        print("[Teclado atualizado]:")
        for row in reply_markup.inline_keyboard:
            print("  " + "  ".join(f"[{btn.text}]" for btn in row))
        print()


async def _mock_answer(*args, **kwargs):
    pass


_tg.bot.send_message = _mock_send
_tg.bot.edit_message_reply_markup = _mock_edit
_tg.bot.edit_message_text = _mock_edit
_tg.bot.answer_callback_query = _mock_answer

from app.agent.sara_agent import chat
from app.agent.session import get_session_state, set_session_state
from app.config import USER_ID


def _print_ajuda():
    print("""
Comandos disponíveis:
  :briefing   → simula o briefing matinal
  :planejar   → simula o início do planejamento noturno
  :lembretes  → verifica e dispara lembretes pendentes
  :estado     → mostra o estado atual da sessão
  :resetar    → volta estado para idle
  :ajuda      → mostra esta lista
  sair        → encerra o CLI
""")


async def _run_briefing():
    from app.scheduler.jobs import briefing_diario
    print("[Simulando briefing diário...]\n")
    await briefing_diario(forçar_envio=True)


async def _run_planejar():
    from app.scheduler.jobs import iniciar_planejamento
    print("[Simulando início do planejamento...]\n")
    await iniciar_planejamento()


async def _run_lembretes():
    from app.scheduler.jobs import verificar_lembretes
    print("[Verificando lembretes pendentes...]\n")
    await verificar_lembretes()


COMMANDS = {
    ":briefing": lambda: asyncio.run(_run_briefing()),
    ":planejar": lambda: asyncio.run(_run_planejar()),
    ":lembretes": lambda: asyncio.run(_run_lembretes()),
    ":estado": lambda: print(f"\n[Estado atual]: {get_session_state(USER_ID)}\n"),
    ":resetar": lambda: (set_session_state(USER_ID, "idle"), print("\n[Estado resetado para: idle]\n")),
    ":ajuda": _print_ajuda,
}


print("=" * 50)
print("Sara — Assistente Pessoal (CLI Mode)")
print("Digite ':ajuda' para ver comandos ou 'sair' para encerrar")
print("=" * 50)
print(f"\nSara: Olá! Sou a Sara, sua assistente pessoal. Como posso ajudar? [user_id={USER_ID}]\n")

while True:
    try:
        entrada = input("Você: ").strip()
        if not entrada:
            continue
        if entrada.lower() == "sair":
            print("Sara: Até logo!")
            break
        if entrada in COMMANDS:
            COMMANDS[entrada]()
            continue

        resposta = chat(entrada, user_id=USER_ID)
        print(f"\nSara: {resposta}\n")

    except KeyboardInterrupt:
        print("\nSara: Até logo!")
        break
