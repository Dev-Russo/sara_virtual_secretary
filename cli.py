from app.agent.sara_agent import chat

print("=" * 50)
print("Sara — Assistente Pessoal")
print("Digite 'sair' para encerrar")
print("=" * 50)
print("\nSara: Olá! Sou a Sara, sua assistente pessoal. Como posso ajudar? 😊\n")

while True:
    try:
        entrada = input("Você: ").strip()
        if not entrada:
            continue
        if entrada.lower() == "sair":
            print("Sara: Até logo! 👋")
            break

        resposta = chat(entrada)
        print(f"\nSara: {resposta}\n")

    except KeyboardInterrupt:
        print("\nSara: Até logo! 👋")
        break