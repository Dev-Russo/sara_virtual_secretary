"""
Agente principal da Sara.

Responsável por:
- Montar o contexto de cada conversa (histórico + mensagem atual)
- Fazer as chamadas à API da Anthropic
- Executar as tools quando o modelo solicitar (ou forçar por keywords)
- Salvar o histórico de conversa no banco
- Registrar audit logs de todas as tool calls
- Validar resposta do LLM contra resultados das tools (grounding)

Fluxo de uma mensagem:
    1. Carrega histórico do banco
    2. Verifica se a mensagem requer tool forçada por keywords
    3. Se sim: executa a tool diretamente e injeta resultado no contexto
    4. Se não: monta lista de mensagens [histórico + mensagem atual]
    5. Primeira chamada Anthropic — modelo decide: responder ou usar tool
    6. Se usar tool: valida argumentos, executa, loga audit, adiciona resultado
    7. Segunda chamada Anthropic — modelo formula resposta final
    8. Verifica grounding: resposta contém dados reais da tool?
    9. Salva a troca no histórico e retorna a resposta
"""

import json
import logging
import re
from datetime import datetime

import pytz
import anthropic

from app.agent.tools import (
    TOOLS_MAP,
    TOOLS_SCHEMA,
    PLANNING_TOOLS_SCHEMA,
    _validar_argumentos,
    TIMEZONE,
)
from app.agent.prompts import get_system_prompt, get_planning_prompt
from app.agent.session import get_session_state
from app.db.database import SessionLocal
from app.models.conversation import ConversationHistory
from app.models.tool_call_log import ToolCallLog
from app.config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    ANTHROPIC_MAX_TOKENS,
)

logger = logging.getLogger(__name__)

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ============================================================
# FORCED TOOL ROUTING — Keywords que bypassam decisão do LLM
# ============================================================

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

COMPLETE_ALL_KEYWORDS = [
    r"\bmarqu[ea]\s+todas\b",
    r"\bmarcar?\s+todas\b",
    r"\bconcluir\s+todas\b",
    r"\bfiz\s+tudo\b",
    r"\bterminez?\s+tudo\b",
    r"\bmarcar?\s+tudo\s+como\s+conclu",
    r"\btodas.*tarefas.*conclu",
    r"\btodas\s+como\s+conclu",
]

START_PLANNING_KEYWORDS = [
    r"^/planejar$",
    r"\bvamos\s+planejar\b",
    r"\bquero\s+planejar\b",
    r"\bme\s+ajuda\s+a\s+planejar\b",
    r"\bplanej[ae]\s+meu\s+(dia|amanhã|próximo\s+dia)\b",
    r"\binicia[r]?\s+(o\s+)?planejamento\b",
]


# Negação ampla — pega "não quero/vou/posso planejar/planjar/planear/planeja/programar"
_NEGATE_PLANNING = [
    r"\bn[aã]o\s+(quero|vou|posso|preciso|tenho)\s+(plan|prog)",
    r"\bn[aã]o\s+(t[oô])\s+a\s+fim\s+de\s+(plan|prog)",
]

# Frases de saída/cancelamento durante planejamento ou revisão
_EXIT_PLANNING = [
    r"\bcancela[r]?\b",
    r"\bdesist[oi]\b",
    r"\bdeixa\s+(pra|para)\s+(l[aá]|depois|amanh[aã]|outra\s+hora)\b",
    r"\bdepois\s+eu\s+(fa[çc]o|vejo|planejo)\b",
    r"\bn[aã]o\s+(quero|vou|posso)\s+(fazer|isso|agora|nada|hoje)\b",
    r"\bp[aá]r[ao]\s+com\s+isso\b",
    r"\bme\s+deixa\s+em\s+paz\b",
    r"\bagora\s+n[aã]o\b",
]

# Confirmações afirmativas curtas — usadas no safety net de "quit confirmation"
_AFIRMATIVAS_CURTAS = [
    r"^sim\b", r"^isso\b", r"^isso\s+mesmo\b", r"^pode\b", r"^pode\s+ser\b",
    r"^pode\s+sim\b", r"^ok\b", r"^aham\b", r"^claro\b", r"^certo\b",
    r"^t[aá](\s+bom)?$", r"^[ée]\s+isso\b", r"^quero\s+sim\b",
    r"^perfeito\b", r"^bora\b", r"^vamos\b", r"^confirmo\b", r"^uhum\b",
]


def _quer_iniciar_planejamento(mensagem: str) -> bool:
    msg_lower = mensagem.lower().strip()
    for negate in _NEGATE_PLANNING:
        if re.search(negate, msg_lower):
            return False
    for pattern in START_PLANNING_KEYWORDS:
        if re.search(pattern, msg_lower):
            return True
    return False


def _quer_sair_planejamento(mensagem: str) -> bool:
    """Detecta frases explícitas de cancelamento/saída do planejamento."""
    msg_lower = mensagem.lower().strip()
    for negate in _NEGATE_PLANNING:
        if re.search(negate, msg_lower):
            return True
    for pattern in _EXIT_PLANNING:
        if re.search(pattern, msg_lower):
            return True
    return False


def _confirmou_saida(historico: list, mensagem: str) -> bool:
    """
    Safety net para o bug em que a IA pergunta 'tem certeza que quer encerrar?'
    e o usuário responde 'sim', mas a IA volta a pedir o plano.

    Detecta: última fala do bot mencionou encerrar/sair E msg atual é afirmação curta.
    """
    last_asst = None
    for h in reversed(historico):
        if h.get("role") == "assistant":
            last_asst = (h.get("content") or "").lower()
            break
    if not last_asst:
        return False

    pediu_confirmacao_saida = any(
        kw in last_asst for kw in (
            "encerrar", "encerra ", "sem planejar", "sair sem", "parar por aqui",
            "deixar pra lá", "deixar para lá",
        )
    )
    if not pediu_confirmacao_saida:
        return False

    msg = mensagem.lower().strip()
    return any(re.search(p, msg) for p in _AFIRMATIVAS_CURTAS)


def _precisa_concluir_todas(mensagem: str) -> bool:
    msg_lower = mensagem.lower().strip()
    for pattern in COMPLETE_ALL_KEYWORDS:
        if re.search(pattern, msg_lower):
            return True
    return False


def _precisa_listar_tarefas(mensagem: str) -> bool:
    msg_lower = mensagem.lower().strip()
    for pattern in LIST_TASK_KEYWORDS:
        if re.search(pattern, msg_lower):
            return True
    return False


def _calcular_data_filtro(mensagem: str) -> str | None:
    from datetime import timedelta
    from app.agent.tools import hoje_logico

    msg_lower = mensagem.lower().strip()
    hoje = hoje_logico()

    if re.search(r"\bhoje\b", msg_lower):
        return hoje.strftime("%Y-%m-%d")

    if re.search(r"\bamanh[aã]\b", msg_lower):
        return (hoje + timedelta(days=1)).strftime("%Y-%m-%d")

    if re.search(r"\bontem\b", msg_lower):
        return (hoje - timedelta(days=1)).strftime("%Y-%m-%d")

    return None


# ============================================================
# HISTÓRICO
# ============================================================

def carregar_historico(user_id: str) -> list[dict]:
    db = SessionLocal()
    try:
        registros = (
            db.query(ConversationHistory)
            .filter(
                ConversationHistory.user_id == user_id,
                ConversationHistory.role.in_(("user", "assistant")),
            )
            .order_by(ConversationHistory.created_at.desc())
            .limit(10)
            .all()
        )
        registros.reverse()
        return [{"role": r.role, "content": str(r.content)} for r in registros]
    except Exception as e:
        logger.error(f"[carregar_historico] {e}")
        return []
    finally:
        db.close()


def limpar_historico_planning(user_id: str) -> None:
    """Remove os turns da sessão de planejamento anterior antes de iniciar uma nova."""
    db = SessionLocal()
    try:
        db.query(ConversationHistory).filter(
            ConversationHistory.user_id == user_id,
            ConversationHistory.role.in_(("plan_user", "plan_asst")),
        ).delete(synchronize_session=False)
        db.commit()
    except Exception as e:
        logger.error(f"[limpar_historico_planning] {e}")
        db.rollback()
    finally:
        db.close()


def carregar_historico_planning(user_id: str) -> list[dict]:
    """Carrega apenas os turnos da sessão de planejamento atual (roles plan_user/plan_asst)."""
    db = SessionLocal()
    try:
        registros = (
            db.query(ConversationHistory)
            .filter(
                ConversationHistory.user_id == user_id,
                ConversationHistory.role.in_(("plan_user", "plan_asst")),
            )
            .order_by(ConversationHistory.created_at.desc())
            .limit(20)
            .all()
        )
        registros.reverse()
        # Converte para roles que a Anthropic aceita
        role_map = {"plan_user": "user", "plan_asst": "assistant"}
        return [{"role": role_map[r.role], "content": str(r.content)} for r in registros]
    except Exception as e:
        logger.error(f"[carregar_historico_planning] {e}")
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
# AUDIT LOG
# ============================================================

def _log_tool_call(
    user_id: str,
    tool_name: str,
    arguments: dict,
    result: str,
    llm_response: str | None = None,
    validation_error: str | None = None,
) -> None:
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
# RESPONSE GROUNDING
# ============================================================

def _verificar_grounding(tool_result: str, llm_response: str) -> bool:
    if not tool_result or not llm_response:
        return True

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
        return True

    llm_lower = llm_response.lower()
    encontrados = sum(1 for t in tokens_result if t in llm_lower)
    taxa = encontrados / len(tokens_result) if tokens_result else 0
    return taxa >= 0.3


def _corrigir_resposta_sem_grounding(tool_result: str, user_message: str) -> str:
    if tool_result and "erro" not in tool_result.lower():
        return tool_result
    return f"Sobre '{user_message}': os dados do sistema são os seguintes:\n\n{tool_result}"


# ============================================================
# EXECUÇÃO DE TOOLS
# ============================================================

def executar_tool(
    nome: str, argumentos: dict, user_id: str, llm_response: str | None = None
) -> str:
    argumentos = argumentos or {}
    argumentos["user_id"] = user_id

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
# HELPERS ANTHROPIC
# ============================================================

def _extrair_texto(content: list) -> str:
    """Extrai o texto de uma lista de content blocks da Anthropic."""
    for block in content:
        if hasattr(block, "type") and block.type == "text":
            return block.text
    return ""


# ============================================================
# CICLO DE PLANEJAMENTO
# ============================================================

def _chat_planning(
    mensagem: str,
    user_id: str,
    system_prompt: str,
    tools_schema: list,
    historico: list,
) -> str:
    """
    Processa uma mensagem durante a sessão de planejamento noturno.
    Sara conduz a conversa, salva tarefas e chama finalizar_planejamento ao fim.
    """
    # Safety net — usuário confirmou que quer sair (responde "sim" depois do bot perguntar
    # "tem certeza?"). Força finalizar_planejamento([]) sem depender da IA.
    if _confirmou_saida(historico, mensagem):
        from app.agent.tools import finalizar_planejamento
        finalizar_planejamento(user_id=user_id, tarefas=[])
        resposta = "Beleza, deixei pra lá. Bom descanso! 😊"
        salvar_historico(user_id, "plan_user", mensagem)
        salvar_historico(user_id, "plan_asst", resposta)
        logger.info(f"[Planning] Saída confirmada via safety net para {user_id}.")
        return resposta

    messages = [
        *historico,
        {"role": "user", "content": mensagem},
    ]

    try:
        response = anthropic_client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=ANTHROPIC_MAX_TOKENS,
            system=system_prompt,
            tools=tools_schema,
            messages=messages,
        )

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})

            result_contents: list[dict] = []
            finalizado = False

            for block in response.content:
                if block.type == "tool_use":
                    resultado = executar_tool(block.name, dict(block.input), user_id)
                    result_contents.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": resultado,
                    })
                    if block.name == "finalizar_planejamento":
                        finalizado = True

            messages.append({"role": "user", "content": result_contents})

            response2 = anthropic_client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=ANTHROPIC_MAX_TOKENS,
                system=system_prompt,
                messages=messages,
            )
            resposta = _extrair_texto(response2.content)

            if finalizado:
                logger.info(f"[Planning] Sessão encerrada para {user_id}")
        else:
            resposta = _extrair_texto(response.content)

    except Exception as e:
        logger.error(f"[_chat_planning] Erro: {e}")
        resposta = "Desculpe, tive um problema. Tente novamente."

    salvar_historico(user_id, "plan_user", mensagem)
    salvar_historico(user_id, "plan_asst", resposta)
    return resposta


# ============================================================
# CICLO PRINCIPAL DO AGENTE
# ============================================================

def chat(mensagem: str, user_id: str) -> str:
    historico = carregar_historico(user_id)
    state = get_session_state(user_id)

    # Acionamento manual do planejamento — só dispara se usuário está idle.
    # Se já está em planning/reviewing_tasks, ignora (não reseta histórico).
    if state == "idle" and _quer_iniciar_planejamento(mensagem):
        from app.agent.session import set_session_state
        limpar_historico_planning(user_id)
        set_session_state(user_id, "planning")
        resposta = "Como foi seu dia hoje? Me conta um pouco — isso me ajuda a planejar o próximo."
        salvar_historico(user_id, "plan_user", mensagem)
        salvar_historico(user_id, "plan_asst", resposta)
        logger.info(f"[Forced routing] Planejamento iniciado manualmente por {user_id}")
        return resposta

    # Revisão de tarefas via inline keyboard — usuário pode sair por texto
    if state == "reviewing_tasks":
        if _quer_sair_planejamento(mensagem):
            from app.agent.session import set_session_state
            set_session_state(user_id, "idle")
            logger.info(f"[Forced routing] Saída de reviewing_tasks por texto: {user_id}")
            return "Beleza, deixei pra lá. Quando quiser, é só me chamar. 😊"
        return "Use os botões acima para marcar suas tarefas de hoje, depois toque em 'Concluir revisão' para continuar."

    # Modo planejamento: usa histórico isolado para não contaminar contexto normal
    if state == "planning":
        system_prompt = get_planning_prompt(user_id)
        historico_planning = carregar_historico_planning(user_id)
        return _chat_planning(mensagem, user_id, system_prompt, PLANNING_TOOLS_SCHEMA, historico_planning)

    system_prompt = get_system_prompt(user_id)

    # Forced routing: "marcar todas como concluídas"
    if _precisa_concluir_todas(mensagem):
        filter_date = _calcular_data_filtro(mensagem)
        logger.info(f"[Forced routing] Concluindo todas as tarefas (filter_date={filter_date})")
        tool_result = executar_tool(
            "complete_all_tasks", {"filter_date": filter_date} if filter_date else {},
            user_id=user_id,
        )
        salvar_historico(user_id, "user", mensagem)
        salvar_historico(user_id, "assistant", tool_result)
        return tool_result

    # Forced routing: listar tarefas direto do banco
    if _precisa_listar_tarefas(mensagem):
        filter_date = _calcular_data_filtro(mensagem)
        logger.info(f"[Forced routing] Listando tarefas direto do banco (filter_date={filter_date})")
        tool_result = executar_tool(
            "list_tasks", {"filter_date": filter_date} if filter_date else {},
            user_id=user_id,
        )

        # Embed tool result in user message — não usa histórico para evitar alucinação
        context = (
            f"{mensagem}\n\n"
            f"[DADOS DO BANCO — use EXATAMENTE estes dados, não invente nada]\n"
            f"{tool_result}\n\n"
            f"Formate de forma amigável. NÃO invente tarefas que não estão acima. "
            f"NÃO omita tarefas que estão acima."
        )

        try:
            response = anthropic_client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=ANTHROPIC_MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": context}],
            )
            resposta = _extrair_texto(response.content)
        except Exception as e:
            logger.error(f"[chat] Erro na chamada Anthropic (forced routing): {e}")
            resposta = _corrigir_resposta_sem_grounding(tool_result, mensagem)

        salvar_historico(user_id, "user", mensagem)
        salvar_historico(user_id, "assistant", resposta)
        return resposta

    # ========================================
    # Caminho normal: LLM decide se usa tool
    # ========================================
    messages = [
        *historico,
        {"role": "user", "content": mensagem},
    ]

    try:
        # Primeira chamada — modelo decide usar tool ou responder
        response = anthropic_client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=ANTHROPIC_MAX_TOKENS,
            system=system_prompt,
            tools=TOOLS_SCHEMA,
            messages=messages,
        )

        # CAMINHO 1 — modelo quer usar tool(s)
        if response.stop_reason == "tool_use":
            # Adiciona resposta do assistente (com os tool_use blocks) ao histórico de mensagens
            messages.append({"role": "assistant", "content": response.content})

            tool_results: list[str] = []
            result_contents: list[dict] = []
            tools_usadas: set[str] = set()

            for block in response.content:
                if block.type == "tool_use":
                    nome = block.name
                    argumentos = dict(block.input)
                    resultado = executar_tool(nome, argumentos, user_id)
                    tool_results.append(resultado)
                    tools_usadas.add(nome)
                    result_contents.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": resultado,
                    })

            messages.append({"role": "user", "content": result_contents})

            # Para operações de escrita, reforça no system prompt da segunda chamada
            WRITE_TOOLS = {"save_task", "create_reminder", "complete_task"}
            system2 = system_prompt
            if tools_usadas & WRITE_TOOLS:
                system2 += (
                    "\n\nINSTRUÇÃO OBRIGATÓRIA PARA ESTA RESPOSTA: Confirme APENAS a operação "
                    "acima que acabou de ser executada. NÃO mencione outras tarefas, lembretes "
                    "ou histórico. NÃO invente contexto adicional. Seja direto e objetivo."
                )

            # Segunda chamada — formula resposta final
            response2 = anthropic_client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=ANTHROPIC_MAX_TOKENS,
                system=system2,
                messages=messages,
            )
            resposta = _extrair_texto(response2.content)

            # Verifica grounding
            combined_tool_result = "\n".join(tool_results)
            if not _verificar_grounding(combined_tool_result, resposta):
                logger.warning(
                    f"[Grounding] Resposta não corresponde aos dados da tool. "
                    f"Tool: {combined_tool_result[:100]}... | LLM: {resposta[:100]}..."
                )
                resposta = _corrigir_resposta_sem_grounding(combined_tool_result, mensagem)

        # CAMINHO 2 — resposta direta sem tools
        else:
            resposta = _extrair_texto(response.content)

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
