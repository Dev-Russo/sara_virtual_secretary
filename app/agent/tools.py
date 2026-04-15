"""
Ferramentas (tools) disponíveis para o agente da Sara.

Cada tool é uma função Python pura que executa uma ação no banco de dados
e retorna uma string descrevendo o resultado. Essa string volta para o modelo,
que a usa para formular a resposta final ao usuário.

Também exportamos TOOLS_SCHEMA — a descrição das tools no formato JSON que
o Groq entende. É esse schema que o modelo lê para saber quando e como
chamar cada função.
"""

import json
import logging
from datetime import datetime

import pytz

from app.db.database import SessionLocal
from app.models.task import Task
from app.models.reminder import Reminder
from app.models.tool_call_log import ToolCallLog

logger = logging.getLogger(__name__)

# Timezone centralizado — todas as datas usam este TZ
TIMEZONE = pytz.timezone("America/Sao_Paulo")

# Constantes de validação
VALID_PRIORITIES = ("low", "medium", "high")
MAX_TITLE_LENGTH = 500
MAX_MESSAGE_LENGTH = 1000


# ============================================================
# VALIDAÇÃO DE ARGUMENTOS
# ============================================================

def _validar_argumentos(tool_name: str, argumentos: dict) -> str | None:
    """
    Valida os argumentos de uma tool antes de executar.
    Retorna None se válido, ou string de erro se inválido.
    """
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

    return None  # Sem erros de validação


# ============================================================
# FUNÇÕES DAS TOOLS
# ============================================================

def save_task(title: str, user_id: str, due_date: str = None, priority: str = "medium") -> str:
    """
    Salva uma nova tarefa no banco de dados.

    Args:
        title: Título ou descrição da tarefa.
        user_id: Identificador do usuário.
        due_date: Data/hora no formato 'YYYY-MM-DD HH:MM'. Opcional.
        priority: Prioridade — 'low', 'medium' ou 'high'.

    Returns:
        String confirmando o salvamento ou descrevendo o erro.
    """
    db = SessionLocal()
    try:
        parsed_date = None
        if due_date and isinstance(due_date, str) and due_date.strip():
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    parsed_naive = datetime.strptime(due_date.strip(), fmt)
                    # #3A — Converte para timezone-aware antes de salvar
                    parsed_date = TIMEZONE.localize(parsed_naive)
                    break
                except ValueError:
                    continue

        task = Task(
            user_id=user_id,
            title=title,
            due_date=parsed_date,
            priority=priority,
            status="pending"
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
    """
    Cria um lembrete para ser disparado em um horário específico.

    #3A — Agora salva o datetime como timezone-aware para evitar
    mismatch com o scheduler que compara com datetime.now(TIMEZONE).

    Args:
        message: Texto do lembrete.
        user_id: Identificador do usuário.
        remind_at: Data/hora no formato 'YYYY-MM-DD HH:MM'.

    Returns:
        String confirmando a criação ou descrevendo o erro.
    """
    db = SessionLocal()
    try:
        parsed_naive = datetime.strptime(remind_at.strip(), "%Y-%m-%d %H:%M")
        # #3A — Converte para timezone-aware
        parsed_date = TIMEZONE.localize(parsed_naive)

        reminder = Reminder(
            user_id=user_id,
            message=message,
            remind_at=parsed_date,
            sent=False
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
    """
    Lista as tarefas pendentes do usuário.

    Args:
        user_id: Identificador do usuário.
        filter_date: Filtra tarefas de uma data específica 'YYYY-MM-DD'. Opcional.

    Returns:
        String com a lista de tarefas ou mensagem de lista vazia.
    """
    db = SessionLocal()
    try:
        query = db.query(Task).filter(
            Task.user_id == user_id,
            Task.status == "pending"
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
                pass  # ignora filtro inválido

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
                    # Banco gravou em UTC sem offset — converte corretamente
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
    """
    Marca todas as tarefas pendentes do usuário como concluídas.
    Se filter_date fornecido, marca apenas as tarefas daquele dia.
    """
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
    """
    Marca uma tarefa como concluída buscando pelo título.

    Args:
        title: Título ou trecho do título da tarefa.
        user_id: Identificador do usuário.

    Returns:
        String confirmando a conclusão ou informando que não foi encontrada.
    """
    db = SessionLocal()
    try:
        task = db.query(Task).filter(
            Task.user_id == user_id,
            Task.status == "pending",
            Task.title.ilike(f"%{title}%")
        ).first()

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


# ============================================================
# MAPA DE TOOLS
# ============================================================

TOOLS_MAP: dict[str, callable] = {
    "save_task": save_task,
    "create_reminder": create_reminder,
    "list_tasks": list_tasks,
    "complete_task": complete_task,
    "complete_all_tasks": complete_all_tasks,
}


# ============================================================
# SCHEMA DAS TOOLS
# ============================================================

TOOLS_SCHEMA: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "save_task",
            "description": (
                "Salva uma nova tarefa no banco de dados. "
                "Use quando o usuário mencionar algo que precisa fazer, "
                "uma obrigação, um compromisso ou qualquer coisa que não pode esquecer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Título ou descrição da tarefa."
                    },
                    "due_date": {
                        "type": "string",
                        "description": "Data e hora no formato 'YYYY-MM-DD HH:MM'. Deixe null se o usuário NÃO informou data — nunca invente uma data."
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Prioridade da tarefa."
                    }
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_reminder",
            "description": (
                "Cria um lembrete para ser disparado em horário específico. "
                "Use quando o usuário pedir para ser lembrado de algo em uma data ou hora."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Texto do lembrete a ser enviado ao usuário."
                    },
                    "remind_at": {
                        "type": "string",
                        "description": "Data e hora exata no formato 'YYYY-MM-DD HH:MM'. SEMPRE use data absoluta (YYYY-MM-DD), nunca use datas relativas."
                    }
                },
                "required": ["message", "remind_at"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": (
                "Lista as tarefas pendentes do usuário. "
                "Use quando o usuário perguntar o que tem para fazer, "
                "quais são suas tarefas ou compromissos."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filter_date": {
                        "type": "string",
                        "description": "Filtra tarefas de uma data específica no formato 'YYYY-MM-DD'. Opcional."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "complete_all_tasks",
            "description": (
                "Marca TODAS as tarefas pendentes como concluídas de uma vez. "
                "Use quando o usuário disser 'marcar todas', 'concluir tudo', 'fiz tudo hoje' ou similar. "
                "Prefira esta tool a chamar complete_task múltiplas vezes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filter_date": {
                        "type": "string",
                        "description": "Filtra apenas tarefas de uma data específica 'YYYY-MM-DD'. Opcional."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": (
                "Marca uma tarefa específica como concluída. "
                "Use quando o usuário mencionar uma tarefa específica que terminou. "
                "Para marcar todas de uma vez, use complete_all_tasks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Título ou trecho do título da tarefa a ser concluída."
                    }
                },
                "required": ["title"]
            }
        }
    }
]
