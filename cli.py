from app.agent.sara_agent import chat
from app.config import USER_ID

print("=" * 50)
print("Sara — Assistente Pessoal (CLI Mode)")
print("Digite 'sair' para encerrar")
print("=" * 50)
print(f"\nSara: Olá! Sou a Sara, sua assistente pessoal. Como posso ajudar? 😊 [user_id={USER_ID}]\n")

while True:
    try:
        entrada = input("Você: ").strip()
        if not entrada:
            continue
        if entrada.lower() == "sair":
            print("Sara: Até logo! 👋")
            break

        resposta = chat(entrada, user_id=USER_ID)
        print(f"\nSara: {resposta}\n")

    except KeyboardInterrupt:
        print("\nSara: Até logo! 👋")
        break