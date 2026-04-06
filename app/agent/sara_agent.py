"""
Agente principal da Sara.

Responsável por:
- Montar o contexto de cada conversa (histórico + mensagem atual)
- Fazer as chamadas à API do Groq
- Executar as tools quando o modelo solicitar
- Salvar o histórico de conversa no banco

Fluxo de uma mensagem:
    1. Carrega histórico do banco
    2. Monta lista de mensagens [system + histórico + mensagem atual]
    3. Primeira chamada ao Groq — modelo decide: responder ou usar tool
    4. Se usar tool: executa a função Python, adiciona resultado às mensagens
    5. Segunda chamada ao Groq — modelo formula resposta final
    6. Salva a troca no histórico e retorna a resposta
"""

import json
from groq import Groq
from app.agent.tools import TOOLS_MAP, TOOLS_SCHEMA
from app.agent.prompts import get_system_prompt
from app.db.database import SessionLocal
from app.models.conversation import ConversationHistory
from app.config import (
    GROQ_API_KEY,
    GROQ_MODEL,
    GROQ_TEMPERATURE,
    GROQ_MAX_TOKENS,
)

groq_client = Groq(api_key=GROQ_API_KEY)

def carregar_historico(user_id: str) -> list[dict]:
    db = SessionLocal()
    try:
        registros = (
            db.query(ConversationHistory)
            .filter(ConversationHistory.user_id == user_id)
            .order_by(ConversationHistory.created_at.desc())
            .limit(10)
            .all()
        )

        # Invertemos para ter ordem cronológica (mais antigas primeiro)
        registros.reverse()

        return [
            {"role": r.role, "content": str(r.content)}
            for r in registros
            if r.role in ("user", "assistant")
        ]

    except Exception as e:
        print(f"[ERRO carregar_historico] {e}")
        return []  # retorna vazio em caso de erro — melhor do que quebrar
    finally:
        db.close()


def salvar_historico(user_id: str, role: str, content: str) -> None:
    db = SessionLocal()
    try:
        registro = ConversationHistory(
            user_id=user_id,
            role=role,
            content=content
        )
        db.add(registro)
        db.commit()

    except Exception as e:
        print(f"[ERRO salvar_historico] {e}")
        db.rollback()
    finally:
        db.close()


# ============================================================
# EXECUÇÃO DE TOOLS
# ============================================================

def executar_tool(nome: str, argumentos: dict, user_id: str) -> str:
    """
    Executa uma tool com base no nome fornecido.
    Injeta automaticamente o user_id em todas as tools.
    """
    funcao = TOOLS_MAP.get(nome)

    if not funcao:
        return f"Tool '{nome}' não encontrada."

    # Garante que argumentos nunca seja None — funções Python não aceitam None como **kwargs
    argumentos = argumentos or {}
    
    # Injeta user_id automaticamente em todas as tools
    argumentos["user_id"] = user_id

    try:
        return funcao(**argumentos)
    except Exception as e:
        print(f"[ERRO executar_tool '{nome}'] {e}")
        return f"Erro ao executar {nome}: {str(e)}"


# ============================================================
# CICLO PRINCIPAL DO AGENTE
# ============================================================

def chat(mensagem: str, user_id: str) -> str:
    """
    Processa uma mensagem do usuário e retorna uma resposta.
    
    Args:
        mensagem: Texto da mensagem do usuário.
        user_id: Identificador do usuário (chat_id do Telegram ou ID fixo do CLI).
    """
    # Carrega as últimas N mensagens para dar contexto ao modelo
    historico = carregar_historico(user_id)

    # Monta a lista de mensagens no formato da API:
    # [system_prompt] + [histórico] + [mensagem atual]
    messages = [
        {"role": "system", "content": get_system_prompt(user_id)},
        *historico,
        {"role": "user", "content": mensagem}
    ]

    try:
        # ----------------------------------------
        # PRIMEIRA CHAMADA — o modelo decide o que fazer
        # tool_choice="auto" significa que o modelo escolhe
        # sozinho se vai usar uma tool ou responder direto
        # ----------------------------------------
        resposta_inicial = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            tools=TOOLS_SCHEMA,
            tool_choice="auto",
            temperature=GROQ_TEMPERATURE,
            max_tokens=GROQ_MAX_TOKENS
        )

        msg = resposta_inicial.choices[0].message

        # ----------------------------------------
        # CAMINHO 1 — modelo quer usar uma ou mais tools
        # ----------------------------------------
        if msg.tool_calls:

            # Adiciona a decisão do modelo ao histórico da conversa
            # (necessário para a segunda chamada entender o contexto)
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    }
                    for tc in msg.tool_calls
                ]
            })

            # Executa cada tool solicitada e adiciona o resultado
            for tool_call in msg.tool_calls:
                nome = tool_call.function.name
                argumentos = json.loads(tool_call.function.arguments)

                print(f"[Tool] {nome}({argumentos})")
                resultado = executar_tool(nome, argumentos, user_id)

                # O resultado da tool entra como mensagem de role "tool"
                # O modelo vai ler isso na segunda chamada
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": resultado
                })

            # ----------------------------------------
            # SEGUNDA CHAMADA — modelo formula resposta
            # com base nos resultados das tools
            # ----------------------------------------
            resposta_final = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                temperature=GROQ_TEMPERATURE,
                max_tokens=GROQ_MAX_TOKENS
            )
            resposta = resposta_final.choices[0].message.content

        # ----------------------------------------
        # CAMINHO 2 — modelo respondeu direto, sem tools
        # ----------------------------------------
        else:
            resposta = msg.content

    except Exception as e:
        import traceback
        traceback.print_exc()
        resposta = f"Desculpe, tive um problema ao processar sua mensagem. Tente novamente. ({str(e)})"

    # Salva a troca no banco independente do caminho tomado
    salvar_historico(user_id, "user", mensagem)
    salvar_historico(user_id, "assistant", resposta)

    return resposta