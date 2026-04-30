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
import unicodedata
import uuid
from datetime import datetime, timedelta, date

import pytz
import anthropic

from app.agent.tools import (
    TOOLS_MAP,
    TOOLS_SCHEMA,
    PLANNING_TOOLS_SCHEMA,
    _validar_argumentos,
    TIMEZONE,
    complete_task_by_id,
    complete_tasks_in_period,
    list_reminders,
    list_tasks,
    resumo_backlog,
    resumo_hoje,
    reschedule_tasks_by_ids,
    reschedule_task,
    save_task,
    save_tasks,
    tarefas_backlog_pendentes,
    tarefas_pendentes_no_periodo,
)
from app.agent.prompts import get_system_prompt, get_planning_prompt
from app.agent.session import get_session_state, get_session_context, set_session_state
from app.agent.copy import (
    HOME_BUTTON_ADICIONAR,
    HOME_BUTTON_BACKLOG,
    HOME_BUTTON_HOJE,
    HOME_BUTTON_LEMBRETES,
    HOME_BUTTON_PLANEJAR,
    HOME_BUTTON_REVISAR,
    mensagem_abertura_planejamento,
    mensagem_atalho_ligado,
    mensagem_cancelamento,
    mensagem_captura_tarefa,
    mensagem_confirmacao_revisao,
    mensagem_home,
    mensagem_pergunta_data_planejamento,
    mensagem_revisao_aplicada,
    mensagem_revisao_backlog_disponivel,
    mensagem_revisao_sem_match,
    mensagem_tarefa_backlog_salva,
)
from app.db.database import SessionLocal
from app.models.conversation import ConversationHistory
from app.models.task import Task
from app.models.tool_call_log import ToolCallLog
from app.config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    ANTHROPIC_MAX_TOKENS,
    CHECKIN_HORA,
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

BULK_COMPLETE_KEYWORDS = [
    r"\bmarqu[ea]\s+todas\b",
    r"\bmarcar?\s+todas\b",
    r"\bconcluir\s+todas\b",
    r"\bconclu[ai]\s+todas\b",
    r"\bcomplete?\s+todas\b",
    r"\bfinaliz[ae]\s+todas\b",
    r"\bfiz\s+tudo\b",
    r"\bterminez?\s+tudo\b",
    r"\bterminei\s+tudo\b",
    r"\bmarcar?\s+tudo\s+como\s+conclu",
    r"\bmarqu[ea]\s+tudo\s+como\s+(feito|conclu)",
    r"\btodas.*tarefas.*conclu",
    r"\btodas\s+como\s+conclu",
]

START_PLANNING_KEYWORDS = [
    r"^/planejar$",
    r"\bvamos\s+planejar\b",
    r"\bquero\s+planejar\b",
    r"\bme\s+ajuda\s+a\s+planejar\b",
    r"\bplanej[ae]\s+meu\s+(dia|amanhã|próximo\s+dia)\b",
    r"\bplanejae\s+meu\s+(dia|amanhã|próximo\s+dia)\b",
    r"\binicia[r]?\s+(o\s+)?planejamento\b",
]

START_CHECK_KEYWORDS = [
    r"^/check$",
    r"\bquero\s+marcar\s+(algumas\s+)?atividades\b",
    r"\bquero\s+revisar\s+minhas\s+atividades\b",
    r"\bmarcar\s+atividades\s+feitas\b",
    r"\brevisar\s+o\s+que\s+fiz\b",
]

ADD_TASK_PATTERNS = [
    r"^(?P<title>.+?)\s+adicione\s+(?:a\s+)?tarefa\b.*$",
    r"^(?:outra\s+tarefa\s+)?(?:para\s+)?adicionar\s+(?:é|eh|:)?\s*(?P<title>.+)$",
    r"^(?:adicione|adicionar|crie|salve)\s+(?:a\s+)?tarefa\s+(?:de\s+)?(?P<title>.+)$",
    r"^(?:adicione|adicionar|crie|salve)\s+(?:para|pra)\s+(?:hoje|amanh[aã])\s+(?P<title>.+)$",
    r"^(?:para|pra)\s+(?:hoje|amanh[aã])\s+(?:adicione|adicionar|crie|salve)\s+(?P<title>.+)$",
    r"^(?:hoje|amanh[aã])\s+(?:preciso|quero|tenho que|tenho de)\s+(?P<title>.+)$",
    r"^(?:preciso|quero|tenho que|tenho de)\s+(?P<title>.+?)\s+(?:hoje|amanh[aã])$",
]

RESCHEDULE_BACKLOG_PATTERNS = [
    r"\b(resgata|resgate|resgatar|move|mova|mover|joga|jogue|jogar|passa|passe|passar|reagenda|reagende|reagendar)\b.*\b(hoje|amanh[aã]|\d{1,2}/\d{1,2}(?:/\d{4})?|\d{4}-\d{2}-\d{2})\b",
    r"\b(hoje|amanh[aã]|\d{1,2}/\d{1,2}(?:/\d{4})?|\d{4}-\d{2}-\d{2})\b.*\b(resgata|resgate|resgatar|move|mova|mover|joga|jogue|jogar|passa|passe|passar|reagenda|reagende|reagendar)\b",
]


# Negação ampla — pega "não quero/vou/posso planejar/planjar/planear/planeja/programar"
_NEGATE_PLANNING = [
    r"\bn[aã]o\s+(quero|vou|posso|preciso|tenho)\s+(plan|prog)",
    r"\bn[aã]o\s+(t[oô])\s+a\s+fim\s+de\s+(plan|prog)",
]

# Frases de saída/cancelamento durante planejamento ou revisão
_EXIT_PLANNING = [
    r"\bencerrar?\b",
    r"\bfechar?\b",
    r"\bsair\b",
    r"\bcancela[r]?\b",
    r"\bdesist[oi]\b",
    r"\bdeixa\s+(pra|para)\s+(l[aá]|depois|amanh[aã]|outra\s+hora)\b",
    r"\bdepois\s+eu\s+(fa[çc]o|vejo|planejo)\b",
    r"\bn[aã]o\s+quero\s+mais\b",
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


def _quer_iniciar_check(mensagem: str) -> bool:
    msg_lower = mensagem.lower().strip()
    for pattern in START_CHECK_KEYWORDS:
        if re.search(pattern, msg_lower):
            return True
    return False


def _home_action(mensagem: str) -> str | None:
    msg = mensagem.strip().lower()
    mapping = {
        HOME_BUTTON_HOJE.lower(): "today",
        HOME_BUTTON_PLANEJAR.lower(): "planning",
        HOME_BUTTON_REVISAR.lower(): "review",
        HOME_BUTTON_BACKLOG.lower(): "backlog",
        HOME_BUTTON_ADICIONAR.lower(): "add_task",
        HOME_BUTTON_LEMBRETES.lower(): "reminders",
        "/start": "home",
        "/home": "home",
    }
    return mapping.get(msg)


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


def _ultima_fala_assistente(historico: list) -> str:
    for h in reversed(historico):
        if h.get("role") == "assistant":
            return (h.get("content") or "").lower()
    return ""


def _confirmou_plano(historico: list, mensagem: str) -> bool:
    last_asst = _ultima_fala_assistente(historico)
    if not last_asst or not _is_affirmative(mensagem):
        return False

    confirmou_plano = any(
        marcador in last_asst
        for marcador in (
            "faz sentido",
            "tudo certo",
            "isso mesmo",
            "tem algo que ajusta",
            "muda na ordem",
            "combina com o ritmo",
        )
    )
    return confirmou_plano


def _precisa_concluir_periodo(mensagem: str) -> bool:
    msg_lower = mensagem.lower().strip()
    if msg_lower in {"tudo", "tudo certo", "ok", "sim"}:
        return False
    for pattern in BULK_COMPLETE_KEYWORDS:
        if re.search(pattern, msg_lower):
            return True
    return False


def _detectar_periodo_conclusao(mensagem: str) -> dict | None:
    msg_lower = mensagem.lower().strip()
    if re.search(r"\bsemana\s+passada\b", msg_lower):
        return {"period": "last_week"}
    if re.search(r"\b(essa|esta)\s+semana\b|\bda\s+semana\b", msg_lower):
        return {"period": "this_week"}
    if re.search(r"\bhoje\b", msg_lower):
        return {"period": "today"}
    if re.search(r"\bontem\b", msg_lower):
        return {"period": "yesterday"}

    data = _parse_data_explicita(mensagem)
    if data:
        return {"start_date": data, "end_date": data}
    return None


def _preparar_confirmacao_conclusao_periodo(user_id: str, periodo: dict) -> str:
    label, tarefas = tarefas_pendentes_no_periodo(user_id=user_id, **periodo)
    if not label:
        return "Não entendi o período. Pode me dizer se é hoje, ontem, esta semana ou uma data específica?"
    if not tarefas:
        set_session_state(user_id, "idle")
        return f"Não achei tarefa pendente em {label}."

    linhas = "\n".join(f"• {task.title}" for task in tarefas)
    set_session_state(
        user_id,
        "confirming_bulk_complete",
        context={"bulk_complete_period": periodo, "bulk_complete_label": label},
        replace_context=True,
    )
    return f"Vou marcar como concluídas as tarefas de {label}:\n\n{linhas}\n\nConfirmo?"


def _tratar_confirmacao_conclusao_periodo(user_id: str, mensagem: str, contexto: dict) -> str:
    if _quer_sair_planejamento(mensagem) or re.search(r"\b(n[aã]o|cancela|deixa)\b", _normalizar(mensagem)):
        set_session_state(user_id, "idle")
        return "Beleza, não mexi nas tarefas."

    periodo = contexto.get("bulk_complete_period")
    if not periodo:
        periodo = _detectar_periodo_conclusao(mensagem)
        if not periodo:
            return "De qual período? Hoje, ontem, esta semana ou uma data específica."
        return _preparar_confirmacao_conclusao_periodo(user_id, periodo)

    if _is_affirmative(mensagem):
        resultado = complete_tasks_in_period(user_id=user_id, **periodo)
        set_session_state(user_id, "idle")
        salvar_historico(user_id, "user", mensagem)
        salvar_historico(user_id, "assistant", resultado)
        return resultado

    novo_periodo = _detectar_periodo_conclusao(mensagem)
    if novo_periodo:
        return _preparar_confirmacao_conclusao_periodo(user_id, novo_periodo)

    return f"Se estiver certo marcar as tarefas de {contexto.get('bulk_complete_label', 'desse período')}, manda um sim. Se não, me fala outro período."


def _precisa_listar_tarefas(mensagem: str) -> bool:
    msg_lower = mensagem.lower().strip()
    for pattern in LIST_TASK_KEYWORDS:
        if re.search(pattern, msg_lower):
            return True
    return False


def _limpar_titulo_extraido(title: str, mensagem: str) -> str:
    title = title.strip(" .")
    title = re.sub(r"\b(por favor|pra mim|para mim|no caso)\b", "", title, flags=re.IGNORECASE).strip(" .")
    for trecho in ("amanhã", "amanha", "hoje", "ontem", "para hoje", "pra hoje", "para amanhã", "pra amanhã", "para amanha", "pra amanha"):
        title = re.sub(rf"\b{trecho}\b", "", title, flags=re.IGNORECASE).strip(" .")
    title = re.sub(r"\s+", " ", title)
    return title


def _split_titulos(titulo: str) -> list[str]:
    partes = re.split(r"\s*,\s*|\s+;\s+|\s+\be\b\s+", titulo)
    return [parte.strip(" .") for parte in partes if len(parte.strip(" .")) >= 3]


def _extrair_tarefas_para_salvar(mensagem: str) -> tuple[list[str], str | None] | None:
    msg_lower = mensagem.lower()
    if "lembrete" in msg_lower:
        return None

    for pattern in ADD_TASK_PATTERNS:
        match = re.search(pattern, mensagem.strip(), flags=re.IGNORECASE)
        if not match:
            continue
        title = _limpar_titulo_extraido(match.group("title"), mensagem)
        due_date = _parse_data_explicita(mensagem)
        titles = _split_titulos(title)
        if titles:
            return titles, due_date
    return None


def _formatar_confirmacao_tarefas_salvas(titles: list[str], due_date: str | None, resultado: str) -> str:
    resultado_lower = resultado.lower()
    if "erro" in resultado_lower or "já exist" in resultado_lower:
        return resultado
    if not due_date:
        if len(titles) == 1:
            return mensagem_tarefa_backlog_salva(titles[0])
        return "Anotei no backlog: " + ", ".join(titles) + ". Depois a gente encaixa isso num dia."
    try:
        display = datetime.strptime(due_date, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        display = due_date
    return f"Anotei para {display}: " + ", ".join(titles) + "."


def _quer_reagendar_backlog(mensagem: str) -> bool:
    msg = _normalizar(mensagem)
    if not re.search(r"\bbacklog\b", msg):
        return False
    return any(re.search(pattern, msg) for pattern in RESCHEDULE_BACKLOG_PATTERNS)


def _calcular_data_filtro(mensagem: str) -> str | None:
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


def _parse_data_explicita(mensagem: str, agora: datetime | None = None) -> str | None:
    if not mensagem:
        return None
    if agora is None:
        agora = datetime.now(TIMEZONE)

    msg = mensagem.lower().strip()
    if re.search(r"\bdepois de amanh[aã]\b", msg):
        return (agora.date() + timedelta(days=2)).strftime("%Y-%m-%d")
    if re.search(r"\bamanh[aã]\b", msg):
        return (agora.date() + timedelta(days=1)).strftime("%Y-%m-%d")
    if re.search(r"\bhoje\b", msg):
        return agora.date().strftime("%Y-%m-%d")

    match_iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", msg)
    if match_iso:
        try:
            return datetime.strptime(match_iso.group(1), "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            return None

    match_br = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b", msg)
    if match_br:
        dia = int(match_br.group(1))
        mes = int(match_br.group(2))
        ano = int(match_br.group(3) or agora.year)
        try:
            parsed = date(ano, mes, dia)
            if match_br.group(3) is None and parsed < agora.date():
                parsed = date(ano + 1, mes, dia)
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            return None

    return None


def _mensagem_inicio_planejamento(target_date: str) -> str:
    return mensagem_abertura_planejamento(target_date)


def _pergunta_data_planejamento() -> str:
    return mensagem_pergunta_data_planejamento()


def _checkin_alcancado(agora: datetime | None = None) -> bool:
    if agora is None:
        agora = datetime.now(TIMEZONE)
    hora, minuto = map(int, CHECKIN_HORA.split(":"))
    corte = agora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    return agora >= corte


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(ch for ch in texto if not unicodedata.combining(ch))


def _frases(texto: str) -> list[str]:
    partes = re.split(r"[,\n;]+|\se\s", _normalizar(texto))
    return [p.strip() for p in partes if p.strip()]


def _buscar_tarefas_revisao(user_id: str, task_ids: list[str]) -> list[Task]:
    if not task_ids:
        return []
    uuids: list[uuid.UUID] = []
    for task_id in task_ids:
        try:
            uuids.append(uuid.UUID(str(task_id)))
        except ValueError:
            continue

    if not uuids:
        return []

    db = SessionLocal()
    try:
        tarefas = (
            db.query(Task)
            .filter(Task.user_id == user_id, Task.id.in_(uuids), Task.status == "pending")
            .all()
        )
        ordem = {str(task_id): idx for idx, task_id in enumerate(task_ids)}
        tarefas.sort(key=lambda task: ordem.get(str(task.id), 9999))
        return tarefas
    finally:
        db.close()


def _serializar_tarefas_revisao(tarefas: list[Task]) -> list[dict]:
    return [{"task_id": str(task.id), "title": task.title} for task in tarefas]


def _contexto_revisao(
    tarefas: list[Task],
    *,
    review_mode: str,
    target_date: str | None = None,
    awaiting_target_date: bool = False,
) -> dict:
    review_tasks = _serializar_tarefas_revisao(tarefas)
    return {
        "review_session_id": uuid.uuid4().hex[:16],
        "review_mode": review_mode,
        "review_task_ids": [task["task_id"] for task in review_tasks],
        "review_tasks": review_tasks,
        "review_task_status_map": {task["task_id"]: False for task in review_tasks},
        "target_date": target_date,
        "awaiting_target_date": awaiting_target_date,
        "review_done": False,
        "remaining_pending": [],
    }


def _perguntar_revisao_backlog(user_id: str) -> str | None:
    tarefas = tarefas_backlog_pendentes(user_id)
    if not tarefas:
        return None
    contexto = {
        "backlog_review_tasks": _serializar_tarefas_revisao(tarefas),
    }
    set_session_state(user_id, "confirming_backlog_review", context=contexto, replace_context=True)
    return mensagem_revisao_backlog_disponivel(contexto["backlog_review_tasks"])


def _iniciar_revisao_backlog(user_id: str, contexto: dict) -> str:
    tarefas = _buscar_tarefas_revisao(
        user_id,
        [task["task_id"] for task in contexto.get("backlog_review_tasks", [])],
    )
    if not tarefas:
        set_session_state(user_id, "idle")
        return "O backlog não tem mais tarefa aberta pra revisar."

    novo_contexto = _contexto_revisao(tarefas, review_mode="check")
    set_session_state(user_id, "reviewing_tasks", context=novo_contexto, replace_context=True)
    itens = "\n".join(f"• {task['title']}" for task in novo_contexto["review_tasks"])
    return f"Fechado. Me fala o que rolou com essas:\n\n{itens}\n\nPode responder livre, tipo 'fiz docker, arquitetura ficou pendente'."


def _tratar_confirmacao_revisao_backlog(user_id: str, mensagem: str, contexto: dict) -> str:
    if _is_affirmative(mensagem):
        return _iniciar_revisao_backlog(user_id, contexto)
    if _quer_sair_planejamento(mensagem) or re.search(r"\b(n[aã]o|deixa|cancela)\b", _normalizar(mensagem)):
        set_session_state(user_id, "idle")
        return "Fechado, não mexi no backlog."
    return "Se quiser revisar o backlog, manda um sim. Se não, pode mandar não."


def _preparar_reagendamento_backlog(user_id: str, mensagem: str) -> str | None:
    target_date = _parse_data_explicita(mensagem)
    if not target_date:
        return None

    tarefas = tarefas_backlog_pendentes(user_id)
    if not tarefas:
        return "Não achei tarefa no backlog pra mover."

    contexto = {
        "reschedule_task_ids": [str(task.id) for task in tarefas],
        "reschedule_tasks": _serializar_tarefas_revisao(tarefas),
        "reschedule_date": target_date,
    }
    set_session_state(user_id, "confirming_reschedule_backlog", context=contexto, replace_context=True)
    linhas = "\n".join(f"{idx}. {task.title}" for idx, task in enumerate(tarefas, start=1))
    display = datetime.strptime(target_date, "%Y-%m-%d").strftime("%d/%m/%Y")
    return (
        f"Quais tarefas do backlog entram em {display}?\n\n"
        f"{linhas}\n\n"
        "Me manda os números ou os nomes. Se forem todas, responde \"todas\"."
    )


def _selecionar_tarefas_reagendamento_backlog(mensagem: str, tarefas: list[dict]) -> list[str]:
    msg = _normalizar(mensagem)
    if re.search(r"\b(todas|todos|tudo)\b", msg):
        return [task["task_id"] for task in tarefas]

    selecionadas: list[str] = []
    numeros = {int(n) for n in re.findall(r"\b\d+\b", msg)}
    for idx, task in enumerate(tarefas, start=1):
        if idx in numeros:
            selecionadas.append(task["task_id"])
            continue
        if any(token and token in msg for token in _task_match_tokens(task)):
            selecionadas.append(task["task_id"])

    return list(dict.fromkeys(selecionadas))


def _tratar_confirmacao_reagendamento_backlog(user_id: str, mensagem: str, contexto: dict) -> str:
    if _quer_sair_planejamento(mensagem) or re.search(r"\b(n[aã]o|cancela|deixa)\b", _normalizar(mensagem)):
        set_session_state(user_id, "idle")
        return "Fechado, não movi nada."

    target_date = contexto.get("reschedule_date")
    tarefas = contexto.get("reschedule_tasks", [])
    if not tarefas:
        tarefas_db = _buscar_tarefas_revisao(user_id, contexto.get("reschedule_task_ids", []))
        tarefas = _serializar_tarefas_revisao(tarefas_db)
        if tarefas:
            set_session_state(
                user_id,
                "confirming_reschedule_backlog",
                context={**contexto, "reschedule_tasks": tarefas},
                replace_context=True,
            )

    task_ids = _selecionar_tarefas_reagendamento_backlog(mensagem, tarefas)

    if not task_ids:
        display = datetime.strptime(target_date, "%Y-%m-%d").strftime("%d/%m/%Y") if target_date else "essa data"
        if re.search(r"\bsem\s+hor[aá]rio\b|\bsem\s+hora\b", _normalizar(mensagem)):
            return f"Fechado, sem horário específico. Quais tarefas entram em {display}? Pode mandar os números, nomes ou \"todas\"."
        return f"Me diz quais tarefas entram em {display}: números, nomes ou \"todas\"."

    resultado = reschedule_tasks_by_ids(task_ids, user_id, target_date)
    set_session_state(user_id, "idle")
    return resultado


def _proxima_data_pendente(contexto: dict) -> str | None:
    target_date = contexto.get("target_date")
    if target_date:
        return target_date
    if contexto.get("review_mode") == "planning":
        return (datetime.now(TIMEZONE).date() + timedelta(days=1)).strftime("%Y-%m-%d")
    return None


def _is_affirmative(mensagem: str) -> bool:
    return any(re.search(p, mensagem.lower().strip()) for p in _AFIRMATIVAS_CURTAS)


def _task_match_tokens(task: dict) -> list[str]:
    base = _normalizar(task.get("title", ""))
    tokens = [base]
    for tok in re.findall(r"[\w]+", base):
        if len(tok) > 3:
            tokens.append(tok)
            if tok.endswith(("ar", "er", "ir")) and len(tok) > 4:
                tokens.append(tok[:-2])
    return list(dict.fromkeys(tokens))


def _resolver_status_fragmento(fragmento: str) -> str | None:
    if re.search(r"\b(mais ou menos|parcial|meio|quase)\b", fragmento):
        return "pending"
    if re.search(r"\b(nao|não|deixei|faltou|falta|pendente|nao fiz|não fiz|nao deu|não deu)\b", fragmento):
        return "pending"
    if re.search(r"\b(fiz|feito|conclui|concluido|terminei|finalizei|ok|rolou)\b", fragmento):
        return "done"
    return None


def _inferir_revisao_por_texto(mensagem: str, tarefas: list[dict]) -> dict[str, bool]:
    msg = _normalizar(mensagem)
    atualizacoes: dict[str, bool] = {}

    if re.search(r"\b(fiz tudo|terminei tudo|deu tudo certo)\b", msg):
        return {task["task_id"]: True for task in tarefas}
    if re.search(r"\b(nao fiz nada|não fiz nada|deixei tudo|nada saiu)\b", msg):
        return {task["task_id"]: False for task in tarefas}

    for fragmento in _frases(mensagem):
        status = _resolver_status_fragmento(fragmento)
        if status is None:
            continue
        for task in tarefas:
            if any(token and token in fragmento for token in _task_match_tokens(task)):
                atualizacoes[task["task_id"]] = status == "done"
    return atualizacoes


def _resumo_revisao(contexto: dict) -> tuple[list[dict], list[dict]]:
    tarefas = contexto.get("review_tasks", [])
    status_map = contexto.get("review_task_status_map", {})
    feitas = [task for task in tarefas if status_map.get(task["task_id"])]
    pendentes = [task for task in tarefas if not status_map.get(task["task_id"])]
    return feitas, pendentes


def _gerar_confirmacao_revisao(user_id: str, contexto: dict) -> str:
    feitas, pendentes = _resumo_revisao(contexto)
    pending_action = contexto.get("pending_action", "move" if contexto.get("review_mode") == "planning" else "keep")
    pending_date = contexto.get("pending_date")
    novo_contexto = {
        **contexto,
        "review_done": True,
        "done_task_ids": [task["task_id"] for task in feitas],
        "pending_task_ids": [task["task_id"] for task in pendentes],
        "pending_action": pending_action,
        "pending_date": pending_date,
    }
    set_session_state(user_id, "review_confirming", context=novo_contexto, replace_context=True)
    return mensagem_confirmacao_revisao(
        [task["title"] for task in feitas],
        [task["title"] for task in pendentes],
        pending_action,
        pending_date,
    )


def _aplicar_revisao(user_id: str, contexto: dict) -> str:
    tarefas = contexto.get("review_tasks", [])
    task_map = {task["task_id"]: task for task in tarefas}
    done_ids = contexto.get("done_task_ids", [])
    pending_ids = contexto.get("pending_task_ids", [])
    pending_action = contexto.get("pending_action", "keep")
    pending_date = contexto.get("pending_date")

    for task_id in done_ids:
        complete_task_by_id(task_id, user_id)

    if pending_action == "move" and pending_date:
        for task_id in pending_ids:
            reschedule_task(task_id, user_id, pending_date)

    resumo = mensagem_revisao_aplicada(
        [task_map[task_id]["title"] for task_id in done_ids if task_id in task_map],
        [task_map[task_id]["title"] for task_id in pending_ids if task_id in task_map],
        pending_date if pending_action == "move" else None,
    )

    if contexto.get("review_mode") == "check":
        set_session_state(user_id, "idle")
        return resumo

    target_date = contexto.get("target_date")
    if target_date:
        abertura = _mensagem_inicio_planejamento(target_date)
        set_session_state(
            user_id,
            "planning",
            context={
                "target_date": target_date,
                "awaiting_target_date": False,
                "review_done": True,
                "remaining_pending": [],
                "review_mode": "planning",
            },
            replace_context=True,
        )
        salvar_historico(user_id, "plan_asst", abertura)
        return f"{resumo}\n\n{abertura}"

    set_session_state(
        user_id,
        "planning",
        context={
            "target_date": None,
            "awaiting_target_date": True,
            "review_done": True,
            "remaining_pending": [],
            "review_mode": "planning",
        },
        replace_context=True,
    )
    return f"{resumo}\n\n{_pergunta_data_planejamento()}"


def toggle_review_task(user_id: str, task_id: str) -> bool:
    contexto = get_session_context(user_id)
    status_map = dict(contexto.get("review_task_status_map", {}))
    if task_id not in status_map:
        return False
    status_map[task_id] = not status_map[task_id]
    set_session_state(
        user_id,
        get_session_state(user_id),
        context={**contexto, "review_task_status_map": status_map},
        replace_context=True,
    )
    return status_map[task_id]


def finalizar_revisao(user_id: str) -> str:
    contexto = get_session_context(user_id)
    return _gerar_confirmacao_revisao(user_id, contexto)


def _tratar_revisao_por_texto(user_id: str, mensagem: str, contexto: dict) -> str:
    tarefas = contexto.get("review_tasks", [])
    if not tarefas:
        set_session_state(user_id, "idle")
        return mensagem_cancelamento()

    atualizacoes = _inferir_revisao_por_texto(mensagem, tarefas)
    if not atualizacoes and _is_affirmative(mensagem):
        return _gerar_confirmacao_revisao(user_id, contexto)
    if not atualizacoes and not re.search(r"\b(fechar|concluir|terminei a revisao|acabei)\b", _normalizar(mensagem)):
        return mensagem_revisao_sem_match()

    status_map = dict(contexto.get("review_task_status_map", {}))
    status_map.update(atualizacoes)
    novo_contexto = {
        **contexto,
        "review_task_status_map": status_map,
    }
    if "pending_date" not in novo_contexto:
        novo_contexto["pending_date"] = _proxima_data_pendente(contexto)
    if "pending_action" not in novo_contexto:
        novo_contexto["pending_action"] = "move" if contexto.get("review_mode") == "planning" else "keep"
    set_session_state(user_id, "reviewing_tasks", context=novo_contexto, replace_context=True)
    return _gerar_confirmacao_revisao(user_id, novo_contexto)


def _tratar_confirmacao_revisao(user_id: str, mensagem: str, contexto: dict) -> str:
    msg = _normalizar(mensagem)
    if _is_affirmative(mensagem):
        return _aplicar_revisao(user_id, contexto)

    if re.search(r"\b(nao move|não move|deixa pendente|mantem pendente|mantem assim)\b", msg):
        novo_contexto = {**contexto, "pending_action": "keep"}
        return _gerar_confirmacao_revisao(user_id, novo_contexto)

    if re.search(r"\b(move|joga|passa|reagenda|amanha|amanhã)\b", msg):
        novo_contexto = {
            **contexto,
            "pending_action": "move",
            "pending_date": _parse_data_explicita(mensagem) or contexto.get("pending_date") or _proxima_data_pendente(contexto),
        }
        return _gerar_confirmacao_revisao(user_id, novo_contexto)

    tarefas = contexto.get("review_tasks", [])
    atualizacoes = _inferir_revisao_por_texto(mensagem, tarefas)
    if atualizacoes:
        status_map = dict(contexto.get("review_task_status_map", {}))
        status_map.update(atualizacoes)
        novo_contexto = {**contexto, "review_task_status_map": status_map}
        return _gerar_confirmacao_revisao(user_id, novo_contexto)

    return "Se quiser ajustar, me fala algo como 'deixa estudo pendente' ou 'move o resto pra amanhã'. Se estiver certo, manda um ok."


def _handle_home_action(action: str, user_id: str, mensagem: str) -> str:
    from app.scheduler.jobs import iniciar_revisao_check

    if action == "home":
        set_session_state(user_id, "idle")
        return mensagem_home()

    if action == "today":
        set_session_state(user_id, "idle")
        return resumo_hoje(user_id)

    if action == "backlog":
        set_session_state(user_id, "idle")
        return resumo_backlog(user_id)

    if action == "reminders":
        set_session_state(user_id, "idle")
        return list_reminders(user_id)

    if action == "add_task":
        set_session_state(
            user_id,
            "adding_task",
            context={"entry_mode": "home_add_task"},
            replace_context=True,
        )
        return mensagem_captura_tarefa()

    if action == "review":
        handled, resposta = iniciar_revisao_check(user_id)
        if handled:
            if resposta == "Hoje não achei tarefa aberta pra revisar.":
                fallback = _perguntar_revisao_backlog(user_id)
                if fallback:
                    return fallback
            return resposta
        return mensagem_atalho_ligado(HOME_BUTTON_REVISAR)

    if action == "planning":
        set_session_state(user_id, "idle")
        if _quer_iniciar_planejamento("/planejar"):
            limpar_historico_planning(user_id)
            agora = datetime.now(TIMEZONE)
            target_date = (agora.date() + timedelta(days=1)).strftime("%Y-%m-%d") if _checkin_alcancado(agora) else None
            if target_date:
                set_session_state(
                    user_id,
                    "planning",
                    context={
                        "target_date": target_date,
                        "awaiting_target_date": False,
                        "review_done": False,
                        "remaining_pending": [],
                    },
                    replace_context=True,
                )
                resposta = _mensagem_inicio_planejamento(target_date)
            else:
                set_session_state(
                    user_id,
                    "planning",
                    context={
                        "target_date": None,
                        "awaiting_target_date": True,
                        "review_done": False,
                        "remaining_pending": [],
                    },
                    replace_context=True,
                )
                resposta = _pergunta_data_planejamento()
            salvar_historico(user_id, "plan_user", mensagem)
            salvar_historico(user_id, "plan_asst", resposta)
            return resposta

    return mensagem_home()


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

    confirmou_plano = _confirmou_plano(historico, mensagem)

    messages = [
        *historico,
        {"role": "user", "content": mensagem},
    ]

    planning_system_prompt = system_prompt
    if confirmou_plano:
        planning_system_prompt += (
            "\n\nINSTRUÇÃO OBRIGATÓRIA PARA ESTA MENSAGEM: "
            "o usuário ACABOU de confirmar o plano. "
            "NÃO reformule o resumo, NÃO pergunte de novo. "
            "Chame finalizar_planejamento imediatamente com a lista completa de tarefas acordadas."
        )

    try:
        response = anthropic_client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=ANTHROPIC_MAX_TOKENS,
            system=planning_system_prompt,
            tools=tools_schema,
            messages=messages,
        )

        if confirmou_plano and response.stop_reason != "tool_use":
            response = anthropic_client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=ANTHROPIC_MAX_TOKENS,
                system=planning_system_prompt + (
                    "\n\nSua resposta anterior estaria errada se não chamasse a tool agora. "
                    "Finalize o plano nesta resposta."
                ),
                tools=tools_schema,
                messages=messages,
            )

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})

            result_contents: list[dict] = []
            finalizado = False
            resultado_finalizacao = ""

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
                        resultado_finalizacao = resultado

            messages.append({"role": "user", "content": result_contents})

            if finalizado:
                resposta = resultado_finalizacao or "Fechei o planejamento."
                logger.info(f"[Planning] Sessão encerrada para {user_id}")
                salvar_historico(user_id, "plan_user", mensagem)
                salvar_historico(user_id, "plan_asst", resposta)
                return resposta

            response2 = anthropic_client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=ANTHROPIC_MAX_TOKENS,
                system=planning_system_prompt,
                messages=messages,
            )
            resposta = _extrair_texto(response2.content)

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
    session_context = get_session_context(user_id)
    home_action = _home_action(mensagem)

    if home_action and state == "idle":
        return _handle_home_action(home_action, user_id, mensagem)

    # Acionamento manual do planejamento — só dispara se usuário está idle.
    # Se já está em planning/reviewing_tasks, ignora (não reseta histórico).
    if state == "idle" and _quer_iniciar_planejamento(mensagem):
        limpar_historico_planning(user_id)
        agora = datetime.now(TIMEZONE)
        target_date = _parse_data_explicita(mensagem, agora)
        if not target_date and _checkin_alcancado(agora):
            target_date = (agora.date() + timedelta(days=1)).strftime("%Y-%m-%d")
        if target_date:
            set_session_state(
                user_id,
                "planning",
                context={
                    "target_date": target_date,
                    "awaiting_target_date": False,
                    "review_done": False,
                    "remaining_pending": [],
                },
                replace_context=True,
            )
            resposta = _mensagem_inicio_planejamento(target_date)
        else:
            set_session_state(
                user_id,
                "planning",
                context={
                    "target_date": None,
                    "awaiting_target_date": True,
                    "review_done": False,
                    "remaining_pending": [],
                },
                replace_context=True,
            )
            resposta = _pergunta_data_planejamento()
        salvar_historico(user_id, "plan_user", mensagem)
        salvar_historico(user_id, "plan_asst", resposta)
        logger.info(f"[Forced routing] Planejamento iniciado manualmente por {user_id}")
        return resposta

    if state == "idle" and _quer_iniciar_check(mensagem):
        from app.scheduler.jobs import iniciar_revisao_check

        logger.info(f"[Forced routing] Revisão manual iniciada por {user_id}")
        handled, resposta = iniciar_revisao_check(user_id)
        if handled:
            if resposta == "Hoje não achei tarefa aberta pra revisar.":
                fallback = _perguntar_revisao_backlog(user_id)
                if fallback:
                    return fallback
            return resposta

    if state == "adding_task":
        if _quer_sair_planejamento(mensagem):
            set_session_state(user_id, "idle")
            return mensagem_cancelamento()
        resultado = save_task(mensagem.strip(), user_id=user_id, due_date=None)
        set_session_state(user_id, "idle")
        titulo = mensagem.strip()
        if "Erro" in resultado or "erro" in resultado.lower():
            return resultado
        if "já existe" in resultado.lower():
            return resultado
        return mensagem_tarefa_backlog_salva(titulo)

    if state == "reviewing_tasks":
        if _quer_sair_planejamento(mensagem):
            set_session_state(user_id, "idle")
            logger.info(f"[Forced routing] Saída de reviewing_tasks por texto: {user_id}")
            return mensagem_cancelamento()
        return _tratar_revisao_por_texto(user_id, mensagem, session_context)

    if state == "reviewing_pending_tasks":
        if _quer_sair_planejamento(mensagem):
            set_session_state(user_id, "idle")
            return mensagem_cancelamento()
        return _tratar_revisao_por_texto(user_id, mensagem, session_context)

    if state == "review_confirming":
        if _quer_sair_planejamento(mensagem):
            set_session_state(user_id, "idle")
            return mensagem_cancelamento()
        return _tratar_confirmacao_revisao(user_id, mensagem, session_context)

    if state == "confirming_bulk_complete":
        return _tratar_confirmacao_conclusao_periodo(user_id, mensagem, session_context)

    if state == "confirming_backlog_review":
        return _tratar_confirmacao_revisao_backlog(user_id, mensagem, session_context)

    if state == "confirming_reschedule_backlog":
        return _tratar_confirmacao_reagendamento_backlog(user_id, mensagem, session_context)

    # Modo planejamento: usa histórico isolado para não contaminar contexto normal
    if state == "planning":
        if _quer_sair_planejamento(mensagem):
            from app.agent.tools import finalizar_planejamento

            finalizar_planejamento(user_id=user_id, tarefas=[])
            salvar_historico(user_id, "plan_user", mensagem)
            salvar_historico(user_id, "plan_asst", mensagem_cancelamento())
            return mensagem_cancelamento()

        if session_context.get("awaiting_target_date"):
            target_date = _parse_data_explicita(mensagem)
            if not target_date:
                return _pergunta_data_planejamento()
            resposta = _mensagem_inicio_planejamento(target_date)
            set_session_state(
                user_id,
                "planning",
                context={**session_context, "target_date": target_date, "awaiting_target_date": False},
                replace_context=True,
            )
            salvar_historico(user_id, "plan_asst", resposta)
            return resposta

        target_date = session_context.get("target_date")
        if not target_date:
            return _pergunta_data_planejamento()

        system_prompt = get_planning_prompt(
            user_id,
            target_date,
            review_done=bool(session_context.get("review_done")),
            remaining_pending=session_context.get("remaining_pending", []),
        )
        historico_planning = carregar_historico_planning(user_id)
        return _chat_planning(mensagem, user_id, system_prompt, PLANNING_TOOLS_SCHEMA, historico_planning)

    system_prompt = get_system_prompt(user_id)

    if session_context.get("last_completed_flow") == "planning" and _is_affirmative(mensagem):
        set_session_state(user_id, "idle")
        resposta = "Fechado."
        salvar_historico(user_id, "user", mensagem)
        salvar_historico(user_id, "assistant", resposta)
        return resposta

    # Forced routing: conclusão em massa só com período explícito + confirmação.
    if _precisa_concluir_periodo(mensagem):
        periodo = _detectar_periodo_conclusao(mensagem)
        if not periodo:
            set_session_state(
                user_id,
                "confirming_bulk_complete",
                context={"bulk_complete_period": None},
                replace_context=True,
            )
            resposta = "De qual período? Hoje, ontem, esta semana ou uma data específica."
        else:
            resposta = _preparar_confirmacao_conclusao_periodo(user_id, periodo)
        salvar_historico(user_id, "user", mensagem)
        salvar_historico(user_id, "assistant", resposta)
        return resposta

    if _quer_reagendar_backlog(mensagem):
        resposta = _preparar_reagendamento_backlog(user_id, mensagem)
        if resposta:
            salvar_historico(user_id, "user", mensagem)
            salvar_historico(user_id, "assistant", resposta)
            return resposta

    pedido_tarefa = _extrair_tarefas_para_salvar(mensagem)
    if pedido_tarefa:
        titles, due_date = pedido_tarefa
        resultado = save_tasks(titles, user_id=user_id, due_date=due_date)
        resposta = _formatar_confirmacao_tarefas_salvas(titles, due_date, resultado)
        salvar_historico(user_id, "user", mensagem)
        salvar_historico(user_id, "assistant", resposta)
        return resposta

    # Forced routing: listar tarefas direto do banco
    if _precisa_listar_tarefas(mensagem):
        filter_date = _calcular_data_filtro(mensagem)
        logger.info(f"[Forced routing] Listando tarefas direto do banco (filter_date={filter_date})")
        tool_result = executar_tool(
            "list_tasks", {"filter_date": filter_date} if filter_date else {},
            user_id=user_id,
        )
        salvar_historico(user_id, "user", mensagem)
        salvar_historico(user_id, "assistant", tool_result)
        return tool_result

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
            WRITE_TOOLS = {"save_task", "create_reminder", "complete_task", "reschedule_task"}
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
