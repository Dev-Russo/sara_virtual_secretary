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
from datetime import datetime, timedelta, date

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
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    parsed = datetime.strptime(str(due_date).strip(), fmt)
                    tz_aware = TIMEZONE.localize(parsed)
                    if tz_aware <= datetime.now(TIMEZONE):
                        return "Erro: a data da tarefa já passou. Forneça uma data futura."
                    break
                except ValueError:
                    continue
            else:
                return "Erro: formato de data inválido. Use 'YYYY-MM-DD' ou 'YYYY-MM-DD HH:MM'."

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

    return None


# ============================================================
# FUNÇÕES DAS TOOLS
# ============================================================

def save_task(title: str, user_id: str, due_date: str = None, priority: str = "medium") -> str:
    db = SessionLocal()
    try:
        parsed_date = None
        if due_date and isinstance(due_date, str) and due_date.strip():
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    parsed_date = TIMEZONE.localize(datetime.strptime(due_date.strip(), fmt))
                    break
                except ValueError:
                    continue

        existente = db.query(Task).filter(
            Task.user_id == user_id,
            Task.status == "pending",
            Task.title.ilike(title.strip()),
        ).first()
        if existente:
            prazo = f" para {existente.due_date.strftime('%d/%m/%Y às %H:%M')}" if existente.due_date else " sem prazo definido"
            return f"Tarefa '{existente.title}' já existe{prazo}."

        task = Task(
            user_id=user_id,
            title=title,
            due_date=parsed_date,
            priority=priority,
            status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        prazo = f" para {parsed_date.strftime('%d/%m/%Y às %H:%M')}" if parsed_date else " sem prazo definido"
        return f"Tarefa '{title}' salva com sucesso{prazo}!"

    except Exception as e:
        db.rollback()
        logger.error(f"[save_task] {e}")
        return f"Erro ao salvar tarefa: {str(e)}"
    finally:
        db.close()


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

        linhas = [f"Você tem {len(tasks)} tarefa(s) pendente(s):"]
        for task in tasks:
            if task.due_date:
                dt = task.due_date
                if dt.tzinfo is None:
                    dt = pytz.utc.localize(dt).astimezone(TIMEZONE)
                else:
                    dt = dt.astimezone(TIMEZONE)
                prazo = f" — {dt.strftime('%d/%m/%Y às %H:%M')}"
            else:
                prazo = " — sem prazo definido"
            prioridade = f" [{task.priority}]" if task.priority != "medium" else ""
            linhas.append(f"• {task.title}{prazo}{prioridade}")

        return "\n".join(linhas)

    except Exception as e:
        logger.error(f"[list_tasks] {e}")
        return f"Erro ao listar tarefas: {str(e)}"
    finally:
        db.close()


def complete_all_tasks(user_id: str, filter_date: str = None) -> str:
    db = SessionLocal()
    try:
        query = db.query(Task).filter(
            Task.user_id == user_id,
            Task.status == "pending",
        )

        if filter_date:
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
            return "Nenhuma tarefa pendente encontrada para marcar como concluída."

        for task in tasks:
            task.status = "done"
            task.updated_at = datetime.now(TIMEZONE)

        db.commit()
        titulos = ", ".join(f"'{t.title}'" for t in tasks)
        return f"Tarefas {titulos} marcadas como concluídas! ✅"

    except Exception as e:
        db.rollback()
        logger.error(f"[complete_all_tasks] {e}")
        return f"Erro ao concluir tarefas: {str(e)}"
    finally:
        db.close()


def complete_task(title: str, user_id: str) -> str:
    db = SessionLocal()
    try:
        task = db.query(Task).filter(
            Task.user_id == user_id,
            Task.status == "pending",
            Task.title.ilike(f"%{title}%"),
        ).first()

        if not task:
            palavras = [p for p in title.split() if len(p) > 3]
            for palavra in palavras:
                task = db.query(Task).filter(
                    Task.user_id == user_id,
                    Task.status == "pending",
                    Task.title.ilike(f"%{palavra}%"),
                ).first()
                if task:
                    break

        if not task:
            return f"Nenhuma tarefa pendente encontrada com '{title}'."

        task.status = "done"
        task.updated_at = datetime.now(TIMEZONE)
        db.commit()

        return f"Tarefa '{task.title}' marcada como concluída! ✅"

    except Exception as e:
        db.rollback()
        logger.error(f"[complete_task] {e}")
        return f"Erro ao concluir tarefa: {str(e)}"
    finally:
        db.close()


def delete_task(title: str, user_id: str) -> str:
    """
    Deleta uma tarefa específica pelo título. Deve ser chamada APENAS após confirmação explícita.
    """
    db = SessionLocal()
    try:
        task = db.query(Task).filter(
            Task.user_id == user_id,
            Task.status == "pending",
            Task.title.ilike(f"%{title}%"),
        ).first()

        if not task:
            palavras = [p for p in title.split() if len(p) > 3]
            for palavra in palavras:
                task = db.query(Task).filter(
                    Task.user_id == user_id,
                    Task.status == "pending",
                    Task.title.ilike(f"%{palavra}%"),
                ).first()
                if task:
                    break

        if not task:
            return f"Nenhuma tarefa pendente encontrada com '{title}'."

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
                for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                    try:
                        parsed_date = TIMEZONE.localize(
                            datetime.strptime(due_date_str.strip(), fmt)
                        )
                        break
                    except ValueError:
                        continue

            existente = db.query(Task).filter(
                Task.user_id == user_id,
                Task.status == "pending",
                Task.title.ilike(title),
            ).first()
            if existente:
                ignoradas.append(title)
                continue

            task = Task(
                user_id=user_id,
                title=title,
                due_date=parsed_date,
                priority=priority,
                status="pending",
            )
            db.add(task)
            salvas.append(title)

        db.commit()
        set_session_state(user_id, "idle")

        if salvas:
            return f"Planejamento finalizado! {len(salvas)} tarefa(s) salva(s): {', '.join(salvas)}"
        if ignoradas:
            return "Planejamento finalizado. Todas as tarefas já existiam no banco."
        return "Sessão de planejamento encerrada."

    except Exception as e:
        db.rollback()
        logger.error(f"[finalizar_planejamento] {e}")
        return f"Erro ao finalizar planejamento: {str(e)}"
    finally:
        db.close()


# ============================================================
# MAPA DE TOOLS
# ============================================================

TOOLS_MAP: dict[str, callable] = {
    "save_task": save_task,
    "create_reminder": create_reminder,
    "list_tasks": list_tasks,
    "complete_task": complete_task,
    "complete_all_tasks": complete_all_tasks,
    "delete_task": delete_task,
    "delete_all_tasks": delete_all_tasks,
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
        "name": "complete_all_tasks",
        "description": (
            "Marca TODAS as tarefas pendentes como concluídas de uma vez. "
            "Use quando o usuário disser 'marcar todas', 'concluir tudo', 'fiz tudo hoje' ou similar. "
            "Prefira esta tool a chamar complete_task múltiplas vezes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filter_date": {
                    "type": "string",
                    "description": "Filtra apenas tarefas de uma data específica 'YYYY-MM-DD'. Opcional.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "complete_task",
        "description": (
            "Marca uma tarefa específica como concluída. "
            "Use quando o usuário mencionar uma tarefa específica que terminou. "
            "Para marcar todas de uma vez, use complete_all_tasks."
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
        "name": "delete_task",
        "description": (
            "Deleta uma tarefa específica pelo título. "
            "NUNCA chame esta tool sem antes perguntar ao usuário 'Tem certeza?' e receber confirmação explícita."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Título ou trecho do título da tarefa a ser deletada.",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "delete_all_tasks",
        "description": (
            "Deleta em massa tarefas pendentes do usuário. "
            "NUNCA chame esta tool sem antes perguntar ao usuário 'Tem certeza?' e receber confirmação explícita. "
            "Suporta filtro por data para deletar apenas tarefas de um dia específico, "
            "ou sem filtro para deletar todas as tarefas pendentes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filter_date": {
                    "type": "string",
                    "description": "Deleta apenas tarefas de uma data específica 'YYYY-MM-DD'. Omita para deletar todas as pendentes.",
                },
            },
            "required": [],
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
    t for t in TOOLS_SCHEMA if t["name"] not in ("save_task", "delete_task", "delete_all_tasks")
] + [FINALIZAR_PLANEJAMENTO_SCHEMA]
