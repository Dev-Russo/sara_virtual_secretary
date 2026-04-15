"""
Agente principal da Sara.

Responsável por:
- Montar o contexto de cada conversa (histórico + mensagem atual)
- Fazer as chamadas à API do Groq
- Executar as tools quando o modelo solicitar (ou forçar por keywords)
- Salvar o histórico de conversa no banco
- Registrar audit logs de todas as tool calls
- Validar resposta do LLM contra resultados das tools (grounding)

Fluxo de uma mensagem:
    1. Carrega histórico do banco
    2. Verifica se a mensagem requer tool forçada por keywords
    3. Se sim: executa a tool diretamente e injeta resultado no contexto
    4. Se não: monta lista de mensagens [system + histórico + mensagem atual]
    5. Primeira chamada ao Groq — modelo decide: responder ou usar tool
    6. Se usar tool: valida argumentos, executa, loga audit, adiciona resultado
    7. Segunda chamada ao Groq — modelo formula resposta final
    8. Verifica grounding: resposta contém dados reais da tool?
    9. Salva a troca no histórico e retorna a resposta
"""

import json
import logging
import re
from datetime import datetime

import pytz
from groq import Groq

from app.agent.tools import (
    TOOLS_MAP,
    TOOLS_SCHEMA,
    _validar_argumentos,
    TIMEZONE,
)
from app.agent.prompts import get_system_prompt
from app.db.database import SessionLocal
from app.models.conversation import ConversationHistory
from app.models.tool_call_log import ToolCallLog
from app.config import (
    GROQ_API_KEY,
    GROQ_MODEL,
    GROQ_TEMPERATURE,
    GROQ_MAX_TOKENS,
)

logger = logging.getLogger(__name__)

groq_client = Groq(api_key=GROQ_API_KEY)

# ============================================================
# FORCED TOOL ROUTING — Keywords que bypassam decisão do LLM
# ============================================================

# Padrões de keywords que indicam que o usuário quer ver dados reais do banco,
# não uma resposta genérica do LLM.
LIST_TASK_KEYWORDS = [
    r"\bminhas\s+tarefas\b",
    r"\bo\s+que\s+tenho\b",
    r"\bo\s+que\s+tenho\s+pra\s+hoje\b",
    r"\bo\s+que\s+tenho\s+para\s+hoje\b",
    r"\bminhas\s+tarefas\s+de\s+hoje\b",
    r"\btarefas\s+pendentes\b",
    r"\blistar\s+tarefas\b",
    r"\blista\s+de\s+tarefas\b",
    r"\bmeus\s+compromissos\b",
    r"\bminha\s+agenda\b",
    r"\bmeus\s+afazeres\b",
    r"\btem\s+alguma\s+tarefa\b",
    r"\btenho\s+alguma\s+tarefa\b",
    r"\bquais\s+são\s+minhas\s+tarefas\b",
]


def _precisa_listar_tarefas(mensagem: str) -> bool:
    """
    Verifica se a mensagem do usuário contém keywords que indicam
    que ele quer uma listagem real do banco (não opinião do LLM).
    """
    msg_lower = mensagem.lower().strip()
    for pattern in LIST_TASK_KEYWORDS:
        if re.search(pattern, msg_lower):
            return True
    return False


def _calcular_data_filtro(mensagem: str) -> str | None:
    """
    #1C — Calcula a data absoluta a partir de referências relativas
    na mensagem do usuário. Não delega isso ao LLM.
    Retorna 'YYYY-MM-DD' ou None se não encontrou referência.
    """
    agora = datetime.now(TIMEZONE)
    msg_lower = mensagem.lower().strip()

    # "hoje"
    if re.search(r"\bhoje\b", msg_lower):
        return agora.strftime("%Y-%m-%d")

    # "amanhã" / "amanha"
    if re.search(r"\bamanh[aã]\b", msg_lower):
        from datetime import timedelta
        amanha = agora + timedelta(days=1)
        return amanha.strftime("%Y-%m-%d")

    # "ontem"
    if re.search(r"\bontem\b", msg_lower):
        from datetime import timedelta
        ontem = agora - timedelta(days=1)
        return ontem.strftime("%Y-%m-%d")

    return None


# ============================================================
# HISTÓRICO
# ============================================================

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
        registros.reverse()
        return [
            {"role": r.role, "content": str(r.content)}
            for r in registros
            if r.role in ("user", "assistant")
        ]
    except Exception as e:
        logger.error(f"[carregar_historico] {e}")
        return []
    finally:
        db.close()


def salvar_historico(user_id: str, role: str, content: str) -> None:
    db = SessionLocal()
    try:
        registro = ConversationHistory(
            user_id=user_id,
            role=role,
            content=content,
        )
        db.add(registro)
        db.commit()
    except Exception as e:
        logger.error(f"[salvar_historico] {e}")
        db.rollback()
    finally:
        db.close()


# ============================================================
# AUDIT LOG — #4A
# ============================================================

def _log_tool_call(
    user_id: str,
    tool_name: str,
    arguments: dict,
    result: str,
    llm_response: str | None = None,
    validation_error: str | None = None,
) -> None:
    """
    Registra uma tool call no banco para auditoria e debugging.
    Executa em sessão separada para não bloquear a resposta.
    """
    db = SessionLocal()
    try:
        log_entry = ToolCallLog(
            user_id=user_id,
            tool_name=tool_name,
            arguments=arguments,
            result=result,
            llm_response=llm_response,
            validation_error=validation_error,
        )
        db.add(log_entry)
        db.commit()
    except Exception as e:
        logger.error(f"[_log_tool_call] Falha ao registrar audit log: {e}")
        db.rollback()
    finally:
        db.close()


# ============================================================
# RESPONSE GROUNDING — #1B, #4C
# ============================================================

def _verificar_grounding(tool_result: str, llm_response: str) -> bool:
    """
    Verifica se a resposta do LLM contém informações reais da tool.
    Extrai palavras-chave do resultado da tool e verifica se aparecem
    na resposta do LLM.

    Retorna True se a resposta está fundamentada em dados reais.
    """
    if not tool_result or not llm_response:
        return True  # Sem dados para comparar

    # Extrai tokens significativos do resultado da tool (ignora palavras comuns)
    palavras_comuns = {
        "o", "a", "as", "os", "de", "da", "do", "das", "dos",
        "para", "por", "com", "sem", "em", "no", "na", "e", "ou",
        "que", "se", "não", "sim", "um", "uma", "é", "foi", "ser",
        "tarefa", "tarefas", "pendente", "pendentes", "sucesso",
        "salva", "salvo", "criado", "marcada", "concluída",
        "nenhuma", "encontrada", "erro", "ao", "você", "tem",
    }

    tokens_result = set()
    for token in re.findall(r"[\wàáâãéêíóôõúüç]+", tool_result.lower()):
        if len(token) > 3 and token not in palavras_comuns:
            tokens_result.add(token)

    if not tokens_result:
        return True  # Resultado muito curto, não há como verificar

    # Conta quantos tokens da tool aparecem na resposta do LLM
    llm_lower = llm_response.lower()
    encontrados = sum(1 for t in tokens_result if t in llm_lower)
    taxa = encontrados / len(tokens_result) if tokens_result else 0

    # Se menos de 30% dos tokens-chave aparecem na resposta, suspect
    return taxa >= 0.3


def _corrigir_resposta_sem_grounding(
    tool_result: str, user_message: str
) -> str:
    """
    Gera uma resposta fallback quando o LLM não usa os dados da tool.
    Usa diretamente o resultado da tool como resposta.
    """
    # Se a tool retornou dados reais, usa diretamente
    if tool_result and "erro" not in tool_result.lower():
        return tool_result
    return f"Sobre '{user_message}': os dados do sistema são os seguintes:\n\n{tool_result}"


# ============================================================
# EXECUÇÃO DE TOOLS — com validação (#4B) e audit log (#4A)
# ============================================================

def executar_tool(
    nome: str, argumentos: dict, user_id: str, llm_response: str | None = None
) -> str:
    """
    Executa uma tool com validação prévia e audit log.

    Fluxo:
        1. Valida argumentos
        2. Se inválido: loga erro e retorna mensagem de erro
        3. Se válido: executa a tool
        4. Registra no audit log
    """
    argumentos = argumentos or {}
    argumentos["user_id"] = user_id

    # #4B — Validação de argumentos antes de executar
    erro_validacao = _validar_argumentos(nome, argumentos)
    if erro_validacao:
        logger.warning(f"[executar_tool] Validação falhou para {nome}: {erro_validacao}")
        _log_tool_call(
            user_id=user_id,
            tool_name=nome,
            arguments=argumentos,
            result="",
            llm_response=llm_response,
            validation_error=erro_validacao,
        )
        return erro_validacao

    # Executa a tool
    funcao = TOOLS_MAP.get(nome)
    if not funcao:
        msg = f"Tool '{nome}' não encontrada."
        _log_tool_call(
            user_id=user_id, tool_name=nome,
            arguments=argumentos, result=msg,
            llm_response=llm_response,
        )
        return msg

    try:
        resultado = funcao(**argumentos)
        logger.info(f"[Tool] {nome}({json.dumps(argumentos, ensure_ascii=False)})")

        # #4A — Audit log
        _log_tool_call(
            user_id=user_id,
            tool_name=nome,
            arguments=argumentos,
            result=resultado,
            llm_response=llm_response,
        )

        return resultado
    except Exception as e:
        logger.error(f"[executar_tool '{nome}'] {e}")
        msg = f"Erro ao executar {nome}: {str(e)}"
        _log_tool_call(
            user_id=user_id, tool_name=nome,
            arguments=argumentos, result=msg,
            llm_response=llm_response,
        )
        return msg


# ============================================================
# CICLO PRINCIPAL DO AGENTE
# ============================================================

def chat(mensagem: str, user_id: str) -> str:
    """
    Processa uma mensagem do usuário e retorna uma resposta.

    Args:
        mensagem: Texto da mensagem do usuário.
        user_id: Identificador do usuário.
    """
    historico = carregar_historico(user_id)
    system_prompt = get_system_prompt(user_id)

    # #1A — Forced tool routing: verifica se precisa listar tarefas direto do banco
    filter_date = None
    if _precisa_listar_tarefas(mensagem):
        filter_date = _calcular_data_filtro(mensagem)
        logger.info(
            f"[Forced routing] Listando tarefas direto do banco"
            f" (filter_date={filter_date})"
        )
        tool_result = executar_tool(
            "list_tasks", {"filter_date": filter_date} if filter_date else {},
            user_id=user_id,
        )

        # Monta contexto com resultado da tool e pede pro LLM formatar.
        # NÃO inclui histórico — o banco é a única fonte de verdade aqui.
        # Incluir histórico faz o LLM usar respostas anteriores como fonte,
        # inventando tarefas que já existiam na conversa mas não no banco.
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": mensagem},
            {
                "role": "system",
                "content": (
                    f"RESULTADO DA CONSULTA AO BANCO — esta é a lista COMPLETA e REAL de tarefas. "
                    f"Use EXATAMENTE estes dados. NÃO adicione tarefas do histórico de conversa.\n\n"
                    f"{tool_result}\n\n"
                    f"Formate esta informação de forma amigável. "
                    f"NÃO invente tarefas que não estão na lista acima. "
                    f"NÃO omita tarefas que estão na lista acima."
                ),
            },
        ]

        try:
            resposta_final = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                temperature=GROQ_TEMPERATURE,
                max_tokens=GROQ_MAX_TOKENS,
            )
            resposta = resposta_final.choices[0].message.content
        except Exception as e:
            logger.error(f"[chat] Erro na chamada ao Groq (forced routing): {e}")
            resposta = _corrigir_resposta_sem_grounding(tool_result, mensagem)

        salvar_historico(user_id, "user", mensagem)
        salvar_historico(user_id, "assistant", resposta)
        return resposta

    # ========================================
    # Caminho normal: LLM decide se usa tool
    # ========================================
    messages = [
        {"role": "system", "content": system_prompt},
        *historico,
        {"role": "user", "content": mensagem},
    ]

    try:
        # Primeira chamada — modelo decide
        resposta_inicial = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            tools=TOOLS_SCHEMA,
            tool_choice="auto",
            temperature=GROQ_TEMPERATURE,
            max_tokens=GROQ_MAX_TOKENS,
        )

        msg = resposta_inicial.choices[0].message

        # CAMINHO 1 — modelo quer usar tool(s)
        if msg.tool_calls:
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            })

            # Coletar resultados das tools para grounding
            tool_results: list[str] = []

            for tool_call in msg.tool_calls:
                nome = tool_call.function.name
                argumentos = json.loads(tool_call.function.arguments)
                resultado = executar_tool(nome, argumentos, user_id)
                tool_results.append(resultado)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": resultado,
                })

            # Segunda chamada — formula resposta com resultados
            # Para operações de escrita, injeta instrução explícita para não inventar contexto extra
            WRITE_TOOLS = {"save_task", "create_reminder", "complete_task"}
            tools_usadas = {tc.function.name for tc in msg.tool_calls}
            if tools_usadas & WRITE_TOOLS:
                messages.append({
                    "role": "system",
                    "content": (
                        "INSTRUÇÃO OBRIGATÓRIA: Confirme APENAS a operação acima que acabou de ser executada. "
                        "NÃO mencione outras tarefas, lembretes ou histórico. "
                        "NÃO invente contexto adicional. Seja direto e objetivo."
                    ),
                })

            resposta_final = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                temperature=GROQ_TEMPERATURE,
                max_tokens=GROQ_MAX_TOKENS,
            )
            resposta = resposta_final.choices[0].message.content

            # #1B/#4C — Verifica grounding
            combined_tool_result = "\n".join(tool_results)
            if not _verificar_grounding(combined_tool_result, resposta):
                logger.warning(
                    f"[Grounding] Resposta do LLM não corresponde aos dados da tool. "
                    f"Tool result: {combined_tool_result[:100]}... | "
                    f"LLM response: {resposta[:100]}..."
                )
                resposta = _corrigir_resposta_sem_grounding(
                    combined_tool_result, mensagem
                )

        # CAMINHO 2 — resposta direta sem tools
        else:
            resposta = msg.content

    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"[chat] Erro: {e}")
        resposta = (
            f"Desculpe, tive um problema ao processar sua mensagem. "
            f"Tente novamente. ({str(e)})"
        )

    salvar_historico(user_id, "user", mensagem)
    salvar_historico(user_id, "assistant", resposta)

    return resposta
