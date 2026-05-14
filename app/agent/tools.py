"""
Ferramentas (tools) disponíveis para o agente da Sara.

Cada tool é uma função Python pura que executa uma ação no banco de dados
e retorna uma string descrevendo o resultado. Essa string volta para o modelo,
que a usa para formular a resposta final ao usuário.

Também exportamos TOOLS_SCHEMA — a descrição das tools no formato JSON que
a Anthropic entende. É esse schema que o modelo lê para saber quando e como
chamar cada função.
"""

import logging
import re
import unicodedata
from datetime import datetime, timedelta, date
import uuid

import pytz

from app.db.database import SessionLocal
from app.models.task import Task
from app.models.reminder import Reminder
from app.agent.session import set_session_state

logger = logging.getLogger(__name__)

TIMEZONE = pytz.timezone("America/Sao_Paulo")

# Dia lógico: antes das 04:00, ainda é "ontem" do ponto de vista do usuário
# (ex: 00:53 de 25/04 → dia lógico = 24/04). Alinha com a percepção humana
# de "dia" (não dormiu = ainda é hoje).
LOGICAL_DAY_CUTOFF_HOUR = 4

VALID_PRIORITIES = ("low", "medium", "high")
TASK_CATEGORIES = ("today", "overdue", "backlog", "upcoming")
CATEGORY_LABELS = {
    "today": "Hoje",
    "overdue": "Atrasadas",
    "backlog": "Backlog",
    "upcoming": "Próximas",
}
MAX_TITLE_LENGTH = 500
MAX_MESSAGE_LENGTH = 1000


def hoje_logico(agora: datetime | None = None) -> date:
    """Data lógica de hoje (com cutoff às 04:00)."""
    if agora is None:
        agora = datetime.now(TIMEZONE)
    if agora.hour < LOGICAL_DAY_CUTOFF_HOUR:
        return (agora - timedelta(days=1)).date()
    return agora.date()


def intervalo_dia_logico(agora: datetime | None = None) -> tuple[datetime, datetime]:
    """Retorna (inicio, fim) timezone-aware do dia lógico atual."""
    if agora is None:
        agora = datetime.now(TIMEZONE)
    ref = agora - timedelta(days=1) if agora.hour < LOGICAL_DAY_CUTOFF_HOUR else agora
    inicio = ref.replace(hour=0, minute=0, second=0, microsecond=0)
    fim = ref.replace(hour=23, minute=59, second=59, microsecond=0)
    return inicio, fim


def _normalizar_titulo(title: str) -> str:
    texto = unicodedata.normalize("NFKD", str(title).lower().strip())
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", texto)


def _due_date_key(valor: datetime | None) -> str | None:
    if not valor:
        return None
    dt = valor
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt).astimezone(TIMEZONE)
    else:
        dt = dt.astimezone(TIMEZONE)
    return dt.strftime("%Y-%m-%d %H:%M")


def _formatar_prazo_tarefa(valor: datetime | None) -> str:
    if not valor:
        return "sem prazo definido"
    dt = valor
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt).astimezone(TIMEZONE)
    else:
        dt = dt.astimezone(TIMEZONE)
    if dt.hour == 0 and dt.minute == 0:
        return dt.strftime("%d/%m/%Y")
    return dt.strftime("%d/%m/%Y às %H:%M")


def _buscar_tarefa_duplicada(db, user_id: str, title: str, due_date: datetime | None) -> Task | None:
    titulo_norm = _normalizar_titulo(title)
    due_key = _due_date_key(due_date)
    tarefas = (
        db.query(Task)
        .filter(Task.user_id == user_id, Task.status == "pending")
        .all()
    )
    for task in tarefas:
        if _normalizar_titulo(task.title) == titulo_norm and _due_date_key(task.due_date) == due_key:
            return task
    return None


def _buscar_tarefas_por_titulo(db, user_id: str, title: str) -> list[Task]:
    title = str(title or "").strip()
    if not title:
        return []

    tarefas = (
        db.query(Task)
        .filter(
            Task.user_id == user_id,
            Task.status == "pending",
            Task.title.ilike(f"%{title}%"),
        )
        .order_by(Task.created_at.asc(), Task.title.asc())
        .all()
    )
    if tarefas:
        return tarefas

    palavras = [p for p in title.split() if len(p) > 3]
    candidatos: dict[str, Task] = {}
    for palavra in palavras:
        encontrados = (
            db.query(Task)
            .filter(
                Task.user_id == user_id,
                Task.status == "pending",
                Task.title.ilike(f"%{palavra}%"),
            )
            .order_by(Task.created_at.asc(), Task.title.asc())
            .all()
        )
        for task in encontrados:
            candidatos[str(task.id)] = task
    return list(candidatos.values())


def buscar_tarefas_pendentes_por_titulo(user_id: str, title: str) -> list[Task]:
    db = SessionLocal()
    try:
        return _buscar_tarefas_por_titulo(db, user_id, title)
    finally:
        db.close()


def buscar_tarefas_datadas_por_titulo(user_id: str, title: str) -> list[Task]:
    db = SessionLocal()
    try:
        tarefas = _buscar_tarefas_por_titulo(db, user_id, title)
        return [task for task in tarefas if task.due_date is not None]
    finally:
        db.close()


def _mensagem_ambiguidade_tarefas(title: str, tarefas: list[Task], acao: str) -> str:
    linhas = "\n".join(f"{idx}. {task.title}" for idx, task in enumerate(tarefas, start=1))
    return (
        f"Encontrei mais de uma tarefa para {acao} com '{title}':\n\n"
        f"{linhas}\n\n"
        "Me fala qual delas pelo número ou por um nome mais específico."
    )


def _conclusao_persistida(db, task_id: uuid.UUID, user_id: str) -> bool:
    tarefa = (
        db.query(Task)
        .filter(
            Task.id == task_id,
            Task.user_id == user_id,
        )
        .first()
    )
    return tarefa is not None and tarefa.status == "done"


def _intervalo_data_local(valor: date) -> tuple[datetime, datetime]:
    inicio = TIMEZONE.localize(datetime.combine(valor, datetime.min.time()))
    fim = TIMEZONE.localize(datetime.combine(valor, datetime.max.time().replace(microsecond=0)))
    return inicio, fim


def _parse_due_date_tarefa(valor: str | None) -> tuple[datetime | None, bool]:
    texto = str(valor or "").strip()
    if not texto:
        return None, False

    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(texto, fmt)
            return TIMEZONE.localize(parsed), fmt == "%Y-%m-%d"
        except ValueError:
            continue
    raise ValueError("invalid_due_date")


def _periodo_para_intervalo(
    period: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[str, datetime, datetime] | None:
    hoje = hoje_logico()
    period_norm = (period or "").strip().lower()

    if start_date:
        try:
            inicio_date = datetime.strptime(start_date.strip(), "%Y-%m-%d").date()
            fim_date = datetime.strptime((end_date or start_date).strip(), "%Y-%m-%d").date()
        except ValueError:
            return None
        inicio, _ = _intervalo_data_local(inicio_date)
        _, fim = _intervalo_data_local(fim_date)
        label = start_date if start_date == (end_date or start_date) else f"{start_date} a {end_date}"
        return label, inicio, fim

    if period_norm == "today":
        inicio, fim = intervalo_dia_logico()
        return "hoje", inicio, fim
    if period_norm == "yesterday":
        inicio, fim = _intervalo_data_local(hoje - timedelta(days=1))
        return "ontem", inicio, fim
    if period_norm == "this_week":
        segunda = hoje - timedelta(days=hoje.weekday())
        inicio, _ = _intervalo_data_local(segunda)
        _, fim = _intervalo_data_local(segunda + timedelta(days=6))
        return "esta semana", inicio, fim
    if period_norm == "last_week":
        segunda = hoje - timedelta(days=hoje.weekday() + 7)
        inicio, _ = _intervalo_data_local(segunda)
        _, fim = _intervalo_data_local(segunda + timedelta(days=6))
        return "semana passada", inicio, fim

    return None


def tarefas_atrasadas_pendentes(user_id: str, agora: datetime | None = None) -> list[Task]:
    if agora is None:
        agora = datetime.now(TIMEZONE)
    inicio, _ = intervalo_dia_logico(agora)
    db = SessionLocal()
    try:
        return (
            db.query(Task)
            .filter(
                Task.user_id == user_id,
                Task.status == "pending",
                Task.due_date != None,
                Task.due_date < inicio,
            )
            .order_by(Task.due_date.asc(), Task.created_at.asc())
            .all()
        )
    finally:
        db.close()


def tarefas_pendentes_no_periodo(
    user_id: str,
    period: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    include_backlog: bool = False,
) -> tuple[str, list[Task]]:
    if (period or "").strip().lower() == "overdue":
        return "atrasadas", tarefas_atrasadas_pendentes(user_id)

    intervalo = _periodo_para_intervalo(period, start_date, end_date)
    if not intervalo:
        return "", []

    label, inicio, fim = intervalo
    db = SessionLocal()
    try:
        query = db.query(Task).filter(
            Task.user_id == user_id,
            Task.status == "pending",
            Task.due_date >= inicio,
            Task.due_date <= fim,
        )
        tasks = query.order_by(Task.due_date.asc(), Task.created_at.asc()).all()

        if include_backlog:
            backlog = (
                db.query(Task)
                .filter(
                    Task.user_id == user_id,
                    Task.status == "pending",
                    Task.due_date == None,
                )
                .order_by(Task.created_at.asc())
                .all()
            )
            tasks.extend(backlog)

        return label, tasks
    finally:
        db.close()


def calcular_categoria(status: str, due_date: datetime | None, agora: datetime | None = None) -> str | None:
    if status != "pending":
        return None
    if due_date is None:
        return "backlog"
    if agora is None:
        agora = datetime.now(TIMEZONE)
    dt = due_date
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt).astimezone(TIMEZONE)
    else:
        dt = dt.astimezone(TIMEZONE)
    inicio, fim = intervalo_dia_logico(agora)
    if dt < inicio:
        return "overdue"
    if dt <= fim:
        return "today"
    return "upcoming"


def atualizar_categoria_tarefa(task: Task, agora: datetime | None = None) -> str | None:
    task.category = calcular_categoria(task.status, task.due_date, agora)
    return task.category


def sincronizar_categorias_pendentes(
    db,
    *,
    user_id: str | None = None,
    agora: datetime | None = None,
) -> int:
    query = db.query(Task).filter(Task.status == "pending")
    if user_id:
        query = query.filter(Task.user_id == user_id)
    tarefas = query.all()
    changed = 0
    for task in tarefas:
        nova = calcular_categoria(task.status, task.due_date, agora)
        if task.category != nova:
            task.category = nova
            changed += 1
    if changed:
        db.commit()
    return changed


def _formatar_linha_tarefa(task: Task) -> str:
    prioridade = f" [{task.priority}]" if task.priority != "medium" else ""
    if task.due_date:
        dt = task.due_date
        if dt.tzinfo is None:
            dt = pytz.utc.localize(dt).astimezone(TIMEZONE)
        else:
            dt = dt.astimezone(TIMEZONE)
        if dt.hour == 0 and dt.minute == 0:
            prazo = f" — {dt.strftime('%d/%m/%Y')}"
        else:
            prazo = f" — {dt.strftime('%d/%m/%Y às %H:%M')}"
    else:
        prazo = ""
    return f"• {task.title}{prazo}{prioridade}"


def _formatar_grupos_tarefas(grupos: dict[str, list[Task]], *, cabecalho: str) -> str:
    secoes: list[str] = []
    for categoria in ("today", "overdue", "backlog", "upcoming"):
        tarefas = grupos.get(categoria, [])
        if not tarefas:
            continue
        linhas = "\n".join(_formatar_linha_tarefa(task) for task in tarefas)
        secoes.append(f"{CATEGORY_LABELS[categoria]}:\n{linhas}")
    if not secoes:
        return "Agora você não tem nada pendente."

    texto = f"{cabecalho}\n\n" + "\n\n".join(secoes)
    if grupos.get("overdue"):
        texto += "\n\nMinha sugestão: resolve a primeira atrasada ou me fala se quer jogar ela pra outro dia."
    return texto


def obter_grupos_tarefas(user_id: str, *, sync: bool = True, agora: datetime | None = None) -> dict[str, list[Task]]:
    db = SessionLocal()
    try:
        if sync:
            sincronizar_categorias_pendentes(db, user_id=user_id, agora=agora)
        tarefas = (
            db.query(Task)
            .filter(Task.user_id == user_id, Task.status == "pending")
            .order_by(Task.due_date.asc().nullslast(), Task.created_at.asc())
            .all()
        )
        grupos = {categoria: [] for categoria in TASK_CATEGORIES}
        for task in tarefas:
            categoria = calcular_categoria(task.status, task.due_date, agora)
            if categoria:
                grupos.setdefault(categoria, []).append(task)
        return grupos
    finally:
        db.close()


def tarefas_backlog_pendentes(user_id: str) -> list[Task]:
    db = SessionLocal()
    try:
        sincronizar_categorias_pendentes(db, user_id=user_id)
        return (
            db.query(Task)
            .filter(
                Task.user_id == user_id,
                Task.status == "pending",
                Task.due_date == None,
            )
            .order_by(Task.created_at.asc(), Task.title.asc())
            .all()
        )
    finally:
        db.close()


def resumo_hoje(user_id: str) -> str:
    grupos = obter_grupos_tarefas(user_id)
    return _formatar_grupos_tarefas(grupos, cabecalho="Hoje tá assim:")


def resumo_backlog(user_id: str) -> str:
    grupos = obter_grupos_tarefas(user_id)
    backlog = grupos.get("backlog", [])
    if not backlog:
        return "Seu backlog tá vazio agora."
    linhas = "\n".join(_formatar_linha_tarefa(task) for task in backlog)
    return f"Seu backlog tá assim:\n\nBacklog:\n{linhas}"


def briefing_do_dia(user_id: str) -> str:
    grupos = obter_grupos_tarefas(user_id)
    if not any(grupos.values()):
        return "Bom dia. Hoje tá mais livre. Se quiser, me chama que eu te ajudo a organizar."
    return "Bom dia. Hoje tá assim:\n\n" + "\n\n".join(
        f"{CATEGORY_LABELS[categoria]}:\n" + "\n".join(_formatar_linha_tarefa(task) for task in grupos[categoria])
        for categoria in ("today", "overdue", "backlog", "upcoming")
        if grupos.get(categoria)
    ) + (
        "\n\nMinha sugestão: resolve a primeira atrasada ou me fala se quer jogar ela pra outro dia."
        if grupos.get("overdue") else ""
    )


# ============================================================
# VALIDAÇÃO DE ARGUMENTOS
# ============================================================

def _validar_argumentos(tool_name: str, argumentos: dict) -> str | None:
    if tool_name == "save_task":
        title = argumentos.get("title", "")
        if not title or not str(title).strip():
            return "Erro: título da tarefa não pode ser vazio."
        if len(str(title)) > MAX_TITLE_LENGTH:
            return f"Erro: título da tarefa muito longo (máximo {MAX_TITLE_LENGTH} caracteres)."

        priority = argumentos.get("priority", "medium")
        if priority not in VALID_PRIORITIES:
            return f"Erro: prioridade '{priority}' inválida. Use: {', '.join(VALID_PRIORITIES)}."

        due_date = argumentos.get("due_date")
        if due_date:
            try:
                parsed_date, date_only = _parse_due_date_tarefa(due_date)
            except ValueError:
                return "Erro: formato de data inválido. Use 'YYYY-MM-DD' ou 'YYYY-MM-DD HH:MM'."
            if parsed_date is None:
                return None
            if date_only:
                if parsed_date.astimezone(TIMEZONE).date() < hoje_logico():
                    return "Erro: a data da tarefa já passou. Forneça uma data futura."
            elif parsed_date <= datetime.now(TIMEZONE):
                return "Erro: a data da tarefa já passou. Forneça uma data futura."

    elif tool_name == "create_reminder":
        message = argumentos.get("message", "")
        if not message or not str(message).strip():
            return "Erro: mensagem do lembrete não pode ser vazia."
        if len(str(message)) > MAX_MESSAGE_LENGTH:
            return f"Erro: mensagem do lembrete muito longa (máximo {MAX_MESSAGE_LENGTH} caracteres)."

        remind_at = argumentos.get("remind_at")
        if not remind_at:
            return "Erro: campo 'remind_at' é obrigatório. Use 'YYYY-MM-DD HH:MM'."

        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(str(remind_at).strip(), fmt)
                tz_aware = TIMEZONE.localize(parsed)
                if tz_aware <= datetime.now(TIMEZONE):
                    return "Erro: o horário do lembrete já passou. Forneça um horário futuro."
                break
            except ValueError:
                continue
        else:
            return "Erro: formato de data inválido. Use 'YYYY-MM-DD HH:MM'."

    elif tool_name == "complete_task":
        title = argumentos.get("title", "")
        if not title or not str(title).strip():
            return "Erro: título da tarefa não pode ser vazio."

    elif tool_name == "complete_tasks_in_period":
        period = str(argumentos.get("period") or "").strip()
        start_date = str(argumentos.get("start_date") or "").strip()
        end_date = str(argumentos.get("end_date") or "").strip()
        backlog_only = bool(argumentos.get("backlog_only"))
        if not period and not start_date and not backlog_only:
            return "Erro: informe o período antes de concluir em massa. Use today, yesterday, this_week, last_week, overdue, backlog ou start_date."
        if period and period not in ("today", "yesterday", "this_week", "last_week", "overdue"):
            return "Erro: período inválido. Use today, yesterday, this_week, last_week ou overdue."
        for field_name, value in (("start_date", start_date), ("end_date", end_date)):
            if value:
                try:
                    datetime.strptime(value, "%Y-%m-%d")
                except ValueError:
                    return f"Erro: {field_name} inválido. Use YYYY-MM-DD."

    elif tool_name == "reschedule_task":
        task_id = argumentos.get("task_id", "")
        if not task_id or not str(task_id).strip():
            return "Erro: task_id é obrigatório."
        try:
            uuid.UUID(str(task_id))
        except ValueError:
            return "Erro: task_id inválido."

        new_due_date = argumentos.get("new_due_date")
        if not new_due_date:
            return "Erro: new_due_date é obrigatório."

        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                datetime.strptime(str(new_due_date).strip(), fmt)
                break
            except ValueError:
                continue
        else:
            return "Erro: formato de data inválido. Use 'YYYY-MM-DD' ou 'YYYY-MM-DD HH:MM'."

    return None


# ============================================================
# FUNÇÕES DAS TOOLS
# ============================================================

def save_task(title: str, user_id: str, due_date: str = None, priority: str = "medium") -> str:
    db = SessionLocal()
    try:
        parsed_date = None
        if due_date and isinstance(due_date, str) and due_date.strip():
            try:
                parsed_date, _ = _parse_due_date_tarefa(due_date)
            except ValueError:
                parsed_date = None

        existente = _buscar_tarefa_duplicada(db, user_id, title.strip(), parsed_date)
        if existente:
            prazo = f" para {_formatar_prazo_tarefa(existente.due_date)}" if existente.due_date else " sem prazo definido"
            return f"Tarefa '{existente.title}' já existe{prazo}."

        task = Task(
            user_id=user_id,
            title=title,
            due_date=parsed_date,
            category=calcular_categoria("pending", parsed_date),
            priority=priority,
            status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        prazo = f" para {_formatar_prazo_tarefa(parsed_date)}" if parsed_date else " sem prazo definido"
        categoria = task.category or "backlog"
        return f"Tarefa '{title}' salva com sucesso{prazo}! Categoria: {categoria}."

    except Exception as e:
        db.rollback()
        logger.error(f"[save_task] {e}")
        return f"Erro ao salvar tarefa: {str(e)}"
    finally:
        db.close()


def save_tasks(
    titles: list[str],
    user_id: str,
    due_date: str = None,
    priority: str = "medium",
) -> str:
    salvas: list[str] = []
    existentes: list[str] = []
    erros: list[str] = []
    for title in titles:
        result = save_task(title=title, user_id=user_id, due_date=due_date, priority=priority)
        result_lower = result.lower()
        if "erro" in result_lower:
            erros.append(result)
        elif "já existe" in result_lower:
            existentes.append(title)
        else:
            salvas.append(title)

    partes: list[str] = []
    if salvas:
        partes.append("Salvei: " + ", ".join(salvas) + ".")
    if existentes:
        partes.append("Já estavam salvas: " + ", ".join(existentes) + ".")
    if erros:
        partes.append("Erros: " + " | ".join(erros))
    return " ".join(partes) if partes else "Nenhuma tarefa foi salva."


def reschedule_tasks_by_ids(task_ids: list[str], user_id: str, new_due_date: str) -> str:
    if not task_ids:
        return "Nenhuma tarefa selecionada para reagendar."

    moved: list[str] = []
    errors: list[str] = []
    for task_id in task_ids:
        result = reschedule_task(str(task_id), user_id, new_due_date)
        if "erro" in result.lower() or "nenhuma tarefa" in result.lower():
            errors.append(result)
        else:
            match = re.search(r"Tarefa '(.+?)' reagendada", result)
            moved.append(match.group(1) if match else str(task_id))

    partes: list[str] = []
    if moved:
        partes.append("Reagendei: " + ", ".join(moved) + ".")
    if errors:
        partes.append("Algumas não foram movidas: " + " | ".join(errors))
    return " ".join(partes) if partes else "Não consegui reagendar as tarefas."


def create_reminder(message: str, user_id: str, remind_at: str) -> str:
    db = SessionLocal()
    try:
        parsed_naive = datetime.strptime(remind_at.strip(), "%Y-%m-%d %H:%M")
        parsed_date = TIMEZONE.localize(parsed_naive)

        reminder = Reminder(
            user_id=user_id,
            message=message,
            remind_at=parsed_date,
            sent=False,
        )
        db.add(reminder)
        db.commit()

        return f"Lembrete criado para {parsed_date.strftime('%d/%m/%Y às %H:%M')}: '{message}'"

    except ValueError:
        return "Formato de data inválido. Use 'YYYY-MM-DD HH:MM'."
    except Exception as e:
        db.rollback()
        logger.error(f"[create_reminder] {e}")
        return f"Erro ao criar lembrete: {str(e)}"
    finally:
        db.close()


def list_tasks(user_id: str, filter_date: str = None) -> str:
    db = SessionLocal()
    try:
        sincronizar_categorias_pendentes(db, user_id=user_id)
        query = db.query(Task).filter(
            Task.user_id == user_id,
            Task.status == "pending",
        )

        if filter_date and isinstance(filter_date, str) and filter_date.strip():
            try:
                date = datetime.strptime(filter_date.strip(), "%Y-%m-%d")
                inicio = TIMEZONE.localize(date.replace(hour=0, minute=0))
                fim = TIMEZONE.localize(date.replace(hour=23, minute=59))
                query = query.filter(
                    Task.due_date >= inicio,
                    Task.due_date <= fim,
                )
            except ValueError:
                pass

        tasks = query.order_by(Task.due_date.asc().nullslast()).all()

        if not tasks:
            if filter_date:
                return f"Nenhuma tarefa encontrada para {filter_date}."
            return "Nenhuma tarefa pendente encontrada."

        if filter_date:
            linhas = [f"Você tem {len(tasks)} tarefa(s) pendente(s):"]
            linhas.extend(_formatar_linha_tarefa(task) for task in tasks)
            return "\n".join(linhas)

        grupos = {categoria: [] for categoria in TASK_CATEGORIES}
        for task in tasks:
            categoria = calcular_categoria(task.status, task.due_date)
            if categoria:
                grupos[categoria].append(task)

        return _formatar_grupos_tarefas(grupos, cabecalho="Seu panorama agora tá assim:")

    except Exception as e:
        logger.error(f"[list_tasks] {e}")
        return f"Erro ao listar tarefas: {str(e)}"
    finally:
        db.close()


def complete_tasks_in_period(
    user_id: str,
    period: str = None,
    start_date: str = None,
    end_date: str = None,
    include_backlog: bool = False,
    backlog_only: bool = False,
    backlog_mode: str | None = None,
    selection_message: str | None = None,
) -> str:
    if backlog_only:
        db = SessionLocal()
        try:
            tasks = (
                db.query(Task)
                .filter(
                    Task.user_id == user_id,
                    Task.status == "pending",
                    Task.due_date == None,
                )
                .order_by(Task.created_at.asc())
                .all()
            )

            if not tasks:
                return "Não achei tarefa pendente no backlog para marcar como concluída."

            for task in tasks:
                task.status = "done"
                task.category = None
                task.updated_at = datetime.now(TIMEZONE)

            db.commit()
            titulos = ", ".join(f"'{t.title}'" for t in tasks)
            return f"Marquei como concluídas as tarefas do backlog: {titulos}."
        except Exception as e:
            db.rollback()
            logger.error(f"[complete_tasks_in_period backlog_only] {e}")
            return f"Erro ao concluir tarefas: {str(e)}"
        finally:
            db.close()

    db = SessionLocal()
    try:
        period_norm = (period or "").strip().lower()
        if period_norm == "overdue":
            inicio, _ = intervalo_dia_logico()
            tasks = (
                db.query(Task)
                .filter(
                    Task.user_id == user_id,
                    Task.status == "pending",
                    Task.due_date != None,
                    Task.due_date < inicio,
                )
                .order_by(Task.due_date.asc(), Task.created_at.asc())
                .all()
            )
            label = "atrasadas"
        else:
            intervalo = _periodo_para_intervalo(period, start_date, end_date)
            if not intervalo:
                return "Me diz o período antes de marcar em massa: hoje, ontem, esta semana, atrasadas, backlog ou uma data específica."

            label, inicio, fim = intervalo
            query = db.query(Task).filter(
                Task.user_id == user_id,
                Task.status == "pending",
                Task.due_date >= inicio,
                Task.due_date <= fim,
            )

            tasks = query.order_by(Task.due_date.asc(), Task.created_at.asc()).all()
        if include_backlog:
            tasks.extend(
                db.query(Task)
                .filter(
                    Task.user_id == user_id,
                    Task.status == "pending",
                    Task.due_date == None,
                )
                .order_by(Task.created_at.asc())
                .all()
            )

        if not tasks:
            return f"Não achei tarefa pendente em {label} para marcar como concluída."

        for task in tasks:
            task.status = "done"
            task.category = None
            task.updated_at = datetime.now(TIMEZONE)

        db.commit()
        titulos = ", ".join(f"'{t.title}'" for t in tasks)
        return f"Marquei como concluídas as tarefas de {label}: {titulos}."

    except Exception as e:
        db.rollback()
        logger.error(f"[complete_tasks_in_period] {e}")
        return f"Erro ao concluir tarefas: {str(e)}"
    finally:
        db.close()


def complete_tasks_by_ids(task_ids: list[str], user_id: str) -> str:
    if not task_ids:
        return "Nenhuma tarefa pendente encontrada para concluir."

    db = SessionLocal()
    try:
        uuids: list[uuid.UUID] = []
        for task_id in task_ids:
            try:
                uuids.append(uuid.UUID(str(task_id)))
            except ValueError:
                continue

        if not uuids:
            return "Nenhuma tarefa pendente encontrada para concluir."

        tasks = (
            db.query(Task)
            .filter(
                Task.user_id == user_id,
                Task.status == "pending",
                Task.id.in_(uuids),
            )
            .all()
        )

        if not tasks:
            return "Nenhuma tarefa pendente encontrada para concluir."

        ordem = {str(task_id): idx for idx, task_id in enumerate(task_ids)}
        tasks.sort(key=lambda task: ordem.get(str(task.id), 9999))

        for task in tasks:
            task.status = "done"
            task.category = None
            task.updated_at = datetime.now(TIMEZONE)

        db.commit()
        titulos = ", ".join(f"'{t.title}'" for t in tasks)
        return f"Marquei como concluídas as tarefas selecionadas do backlog: {titulos}."
    except Exception as e:
        db.rollback()
        logger.error(f"[complete_tasks_by_ids] {e}")
        return f"Erro ao concluir tarefas: {str(e)}"
    finally:
        db.close()


def complete_task(title: str, user_id: str) -> str:
    db = SessionLocal()
    try:
        tarefas = _buscar_tarefas_por_titulo(db, user_id, title)
        if not tarefas:
            return f"Nenhuma tarefa pendente encontrada com '{title}'."
        if len(tarefas) > 1:
            return _mensagem_ambiguidade_tarefas(title, tarefas, "concluir")

        task = tarefas[0]

        task.status = "done"
        task.category = None
        task.updated_at = datetime.now(TIMEZONE)
        db.commit()
        db.refresh(task)

        if not _conclusao_persistida(db, task.id, user_id):
            return (
                f"Tentei concluir '{task.title}', mas não consegui validar a mudança no sistema. "
                "Me pede para listar as pendentes ou tenta concluir pelo item exato."
            )

        return f"Tarefa '{task.title}' marcada como concluída! ✅"

    except Exception as e:
        db.rollback()
        logger.error(f"[complete_task] {e}")
        return f"Erro ao concluir tarefa: {str(e)}"
    finally:
        db.close()


def complete_task_by_id(task_id: str, user_id: str) -> str:
    db = SessionLocal()
    try:
        task = db.query(Task).filter(
            Task.id == uuid.UUID(task_id),
            Task.user_id == user_id,
            Task.status == "pending",
        ).first()

        if not task:
            return "Nenhuma tarefa pendente encontrada para concluir."

        task.status = "done"
        task.category = None
        task.updated_at = datetime.now(TIMEZONE)
        db.commit()

        if not _conclusao_persistida(db, task.id, user_id):
            return (
                f"Tentei concluir '{task.title}', mas não consegui validar a mudança no sistema. "
                "Me pede para revisar os itens abertos que eu te mostro o estado real."
            )

        return f"Tarefa '{task.title}' marcada como concluída! ✅"
    except Exception as e:
        db.rollback()
        logger.error(f"[complete_task_by_id] {e}")
        return f"Erro ao concluir tarefa: {str(e)}"
    finally:
        db.close()


def delete_task(title: str, user_id: str) -> str:
    """
    Deleta uma tarefa específica pelo título. Deve ser chamada APENAS após confirmação explícita.
    """
    db = SessionLocal()
    try:
        tarefas = _buscar_tarefas_por_titulo(db, user_id, title)
        if not tarefas:
            return f"Nenhuma tarefa pendente encontrada com '{title}'."
        if len(tarefas) > 1:
            return _mensagem_ambiguidade_tarefas(title, tarefas, "deletar")

        task = tarefas[0]

        titulo = task.title
        db.delete(task)
        db.commit()
        return f"Tarefa '{titulo}' deletada."

    except Exception as e:
        db.rollback()
        logger.error(f"[delete_task] {e}")
        return f"Erro ao deletar tarefa: {str(e)}"
    finally:
        db.close()


def delete_all_tasks(user_id: str, filter_date: str = None) -> str:
    """
    Deleta tarefas pendentes do usuário. Deve ser chamada APENAS após confirmação explícita.
    Se filter_date fornecido, deleta apenas as tarefas daquele dia.
    Sem filter_date, deleta TODAS as tarefas pendentes do usuário.
    """
    db = SessionLocal()
    try:
        query = db.query(Task).filter(
            Task.user_id == user_id,
            Task.status == "pending",
        )

        if filter_date and isinstance(filter_date, str) and filter_date.strip():
            try:
                date = datetime.strptime(filter_date.strip(), "%Y-%m-%d")
                inicio = TIMEZONE.localize(date.replace(hour=0, minute=0))
                fim = TIMEZONE.localize(date.replace(hour=23, minute=59))
                query = query.filter(
                    Task.due_date >= inicio,
                    Task.due_date <= fim,
                )
            except ValueError:
                pass

        tasks = query.all()

        if not tasks:
            return "Nenhuma tarefa pendente encontrada para deletar."

        titulos = [t.title for t in tasks]
        for task in tasks:
            db.delete(task)
        db.commit()

        return f"{len(titulos)} tarefa(s) deletada(s): {', '.join(titulos)}"

    except Exception as e:
        db.rollback()
        logger.error(f"[delete_all_tasks] {e}")
        return f"Erro ao deletar tarefas: {str(e)}"
    finally:
        db.close()


def delete_tasks_by_ids(task_ids: list[str], user_id: str) -> str:
    if not task_ids:
        return "Nenhuma tarefa pendente encontrada para deletar."

    db = SessionLocal()
    try:
        uuids: list[uuid.UUID] = []
        for task_id in task_ids:
            try:
                uuids.append(uuid.UUID(str(task_id)))
            except ValueError:
                continue

        if not uuids:
            return "Nenhuma tarefa pendente encontrada para deletar."

        tasks = (
            db.query(Task)
            .filter(
                Task.user_id == user_id,
                Task.status == "pending",
                Task.id.in_(uuids),
            )
            .all()
        )

        if not tasks:
            return "Nenhuma tarefa pendente encontrada para deletar."

        ordem = {str(task_id): idx for idx, task_id in enumerate(task_ids)}
        tasks.sort(key=lambda task: ordem.get(str(task.id), 9999))

        titulos = [task.title for task in tasks]
        for task in tasks:
            db.delete(task)
        db.commit()

        return f"{len(titulos)} tarefa(s) deletada(s): {', '.join(titulos)}"
    except Exception as e:
        db.rollback()
        logger.error(f"[delete_tasks_by_ids] {e}")
        return f"Erro ao deletar tarefas: {str(e)}"
    finally:
        db.close()


def reschedule_task(task_id: str, user_id: str, new_due_date: str) -> str:
    db = SessionLocal()
    try:
        task = db.query(Task).filter(
            Task.id == uuid.UUID(task_id),
            Task.user_id == user_id,
            Task.status == "pending",
        ).first()

        if not task:
            return "Nenhuma tarefa pendente encontrada para reagendar."

        parsed_date = None
        used_fmt = None
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed_date = datetime.strptime(new_due_date.strip(), fmt)
                used_fmt = fmt
                break
            except ValueError:
                continue

        if parsed_date is None:
            return "Formato de data inválido. Use 'YYYY-MM-DD' ou 'YYYY-MM-DD HH:MM'."

        if used_fmt == "%Y-%m-%d" and task.due_date:
            atual = task.due_date
            if atual.tzinfo is None:
                atual = pytz.utc.localize(atual).astimezone(TIMEZONE)
            else:
                atual = atual.astimezone(TIMEZONE)
            parsed_date = parsed_date.replace(hour=atual.hour, minute=atual.minute)

        task.due_date = TIMEZONE.localize(parsed_date)
        atualizar_categoria_tarefa(task)
        task.updated_at = datetime.now(TIMEZONE)
        db.commit()

        return (
            f"Tarefa '{task.title}' reagendada para "
            f"{_formatar_prazo_tarefa(task.due_date)}."
        )
    except Exception as e:
        db.rollback()
        logger.error(f"[reschedule_task] {e}")
        return f"Erro ao reagendar tarefa: {str(e)}"
    finally:
        db.close()


def move_task_to_backlog(task_id: str, user_id: str) -> str:
    db = SessionLocal()
    try:
        task = db.query(Task).filter(
            Task.id == uuid.UUID(task_id),
            Task.user_id == user_id,
            Task.status == "pending",
        ).first()

        if not task:
            return "Nenhuma tarefa pendente encontrada para mover para o backlog."

        task.due_date = None
        atualizar_categoria_tarefa(task)
        task.updated_at = datetime.now(TIMEZONE)
        db.commit()
        db.refresh(task)

        if task.status != "pending" or task.due_date is not None or task.category != "backlog":
            return (
                f"Tentei mover '{task.title}' para o backlog, mas não consegui validar a mudança no sistema. "
                "Me pede para listar o backlog ou as tarefas de hoje que eu te mostro o estado real."
            )

        return f"Tarefa '{task.title}' movida para o backlog."
    except Exception as e:
        db.rollback()
        logger.error(f"[move_task_to_backlog] {e}")
        return f"Erro ao mover tarefa para o backlog: {str(e)}"
    finally:
        db.close()


def finalizar_planejamento(user_id: str, tarefas: list = None) -> str:
    """
    Encerra a sessão de planejamento e salva todas as tarefas de uma vez.
    Recebe a lista completa de tarefas acordadas durante a conversa.
    """
    db = SessionLocal()
    try:
        salvas = []
        ignoradas = []

        for t in (tarefas or []):
            if not isinstance(t, dict):
                continue
            title = str(t.get("title", "")).strip()
            if not title:
                continue

            priority = t.get("priority", "medium")
            if priority not in VALID_PRIORITIES:
                priority = "medium"

            due_date_str = t.get("due_date")
            parsed_date = None
            if due_date_str and isinstance(due_date_str, str) and due_date_str.strip():
                try:
                    parsed_date, _ = _parse_due_date_tarefa(due_date_str)
                except ValueError:
                    parsed_date = None

            existente = _buscar_tarefa_duplicada(db, user_id, title, parsed_date)
            if existente:
                ignoradas.append(title)
                continue

            task = Task(
                user_id=user_id,
                title=title,
                due_date=parsed_date,
                category=calcular_categoria("pending", parsed_date),
                priority=priority,
                status="pending",
            )
            db.add(task)
            salvas.append(title)

        db.commit()
        set_session_state(user_id, "idle", context={"last_completed_flow": "planning"})

        if salvas and ignoradas:
            return (
                f"Fechei seu plano. Salvei {len(salvas)} tarefa(s): {', '.join(salvas)}. "
                f"Já estavam salvas para esse dia: {', '.join(ignoradas)}."
            )
        if salvas:
            return f"Fechei seu plano. Salvei {len(salvas)} tarefa(s): {', '.join(salvas)}."
        if ignoradas:
            return "Fechei por aqui. O que você listou já estava salvo."
        return "Fechei por aqui sem salvar tarefa nova."

    except Exception as e:
        db.rollback()
        logger.error(f"[finalizar_planejamento] {e}")
        return f"Erro ao finalizar planejamento: {str(e)}"
    finally:
        db.close()


def list_reminders(user_id: str) -> str:
    db = SessionLocal()
    try:
        agora = datetime.now(TIMEZONE)
        lembretes = (
            db.query(Reminder)
            .filter(
                Reminder.user_id == user_id,
                Reminder.sent == False,
                Reminder.remind_at >= agora,
            )
            .order_by(Reminder.remind_at.asc())
            .all()
        )
        if not lembretes:
            return "Você não tem lembrete pendente agora."

        linhas = ["Seus lembretes que ainda vão tocar:"]
        for lembrete in lembretes:
            dt = lembrete.remind_at
            if dt.tzinfo is None:
                dt = pytz.utc.localize(dt).astimezone(TIMEZONE)
            else:
                dt = dt.astimezone(TIMEZONE)
            linhas.append(f"• {dt.strftime('%d/%m/%Y às %H:%M')} — {lembrete.message}")
        return "\n".join(linhas)
    except Exception as e:
        logger.error(f"[list_reminders] {e}")
        return f"Erro ao listar lembretes: {str(e)}"
    finally:
        db.close()


# ============================================================
# MAPA DE TOOLS
# ============================================================

TOOLS_MAP: dict[str, callable] = {
    "save_task": save_task,
    "create_reminder": create_reminder,
    "list_tasks": list_tasks,
    "list_reminders": list_reminders,
    "complete_task": complete_task,
    "complete_tasks_in_period": complete_tasks_in_period,
    "delete_task": delete_task,
    "delete_all_tasks": delete_all_tasks,
    "reschedule_task": reschedule_task,
    "finalizar_planejamento": finalizar_planejamento,
}


# ============================================================
# SCHEMA DAS TOOLS
# ============================================================

TOOLS_SCHEMA: list[dict] = [
    {
        "name": "save_task",
        "description": (
            "Salva uma nova tarefa no banco de dados. "
            "Use quando o usuário mencionar algo que precisa fazer, "
            "uma obrigação, um compromisso ou qualquer coisa que não pode esquecer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Título ou descrição da tarefa.",
                },
                "due_date": {
                    "type": "string",
                    "description": "Data e hora no formato 'YYYY-MM-DD HH:MM'. Deixe null se o usuário NÃO informou data — nunca invente uma data.",
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Prioridade da tarefa. Use 'medium' por padrão — só use 'high' se o usuário disser que algo é urgente ou prioritário.",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "create_reminder",
        "description": (
            "Cria um lembrete para ser disparado em horário específico. "
            "Use quando o usuário pedir para ser lembrado de algo em uma data ou hora."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Texto do lembrete a ser enviado ao usuário.",
                },
                "remind_at": {
                    "type": "string",
                    "description": "Data e hora exata no formato 'YYYY-MM-DD HH:MM'. SEMPRE use data absoluta, nunca relativa.",
                },
            },
            "required": ["message", "remind_at"],
        },
    },
    {
        "name": "list_tasks",
        "description": (
            "Lista as tarefas pendentes do usuário. "
            "Use quando o usuário perguntar o que tem para fazer, "
            "quais são suas tarefas ou compromissos."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filter_date": {
                    "type": "string",
                    "description": "Filtra tarefas de uma data específica no formato 'YYYY-MM-DD'. Opcional.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "list_reminders",
        "description": "Lista os lembretes futuros do usuário que ainda não foram enviados.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "complete_task",
        "description": (
            "Marca uma tarefa específica como concluída. "
            "Use quando o usuário mencionar uma tarefa específica que terminou. "
            "Para marcar várias de uma vez, não tente concluir em lote por conta própria; peça o período se faltar contexto."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Título ou trecho do título da tarefa a ser concluída.",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "reschedule_task",
        "description": (
            "Reagenda uma tarefa pendente específica já existente. "
            "Use quando a tarefa certa já foi escolhida no fluxo e só falta mover a data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "UUID da tarefa específica que será reagendada.",
                },
                "new_due_date": {
                    "type": "string",
                    "description": "Nova data/hora absoluta no formato 'YYYY-MM-DD' ou 'YYYY-MM-DD HH:MM'.",
                },
            },
            "required": ["task_id", "new_due_date"],
        },
    },
]

FINALIZAR_PLANEJAMENTO_SCHEMA = {
    "name": "finalizar_planejamento",
    "description": (
        "Encerra a sessão de planejamento e salva TODAS as tarefas acordadas de uma vez. "
        "Chame SOMENTE quando o plano do dia seguinte estiver fechado e o usuário tiver confirmado, "
        "ou quando o usuário quiser encerrar sem planejar. "
        "Passe a lista completa de tarefas acordadas durante a conversa no campo 'tarefas'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "tarefas": {
                "type": "array",
                "description": "Lista completa de tarefas acordadas durante a conversa de planejamento.",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Título da tarefa.",
                        },
                        "due_date": {
                            "type": "string",
                            "description": "Data/hora no formato 'YYYY-MM-DD HH:MM'. Omita se o usuário não informou horário — nunca invente.",
                        },
                        "priority": {
                            "type": "string",
                            "enum": ["low", "medium", "high"],
                            "description": "Use 'medium' por padrão. Só 'high' se o usuário disse que algo é urgente.",
                        },
                    },
                    "required": ["title"],
                },
            },
        },
        "required": [],
    },
}

# Tools disponíveis durante o planejamento: sem save_task (salvar é via finalizar_planejamento)
PLANNING_TOOLS_SCHEMA = [
    t for t in TOOLS_SCHEMA if t["name"] not in (
        "save_task",
        "delete_task",
        "delete_all_tasks",
    )
] + [FINALIZAR_PLANEJAMENTO_SCHEMA]
