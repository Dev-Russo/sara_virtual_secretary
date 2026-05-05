import asyncio

# Install fake Telegram transport BEFORE importing anything that sends messages.
from tests.harness.telegram import install_fake_telegram

# Equivalent smoke criterion: install_fake_telegram()
install_fake_telegram(echo=True)

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
