"""
Ferramentas (tools) disponíveis para o agente da Sara.

Cada tool é uma função Python pura que executa uma ação no banco de dados
e retorna uma string descrevendo o resultado. Essa string volta para o modelo,
que a usa para formular a resposta final ao usuário.

Também exportamos TOOLS_SCHEMA — a descrição das tools no formato JSON que
o Groq entende. É esse schema que o modelo lê para saber quando e como
chamar cada função.
"""

from datetime import datetime
from app.db.database import SessionLocal
from app.models.task import Task
from app.models.reminder import Reminder


# ============================================================
# FUNÇÕES DAS TOOLS
# Cada função:
#   - recebe argumentos simples (str, int, etc.)
#   - abre e fecha a própria sessão do banco
#   - retorna uma string com o resultado
#   - nunca levanta exceções — erros viram strings descritivas
# ============================================================

def save_task(title: str, user_id: str, due_date: str = None, priority: str = "medium") -> str:
    """
    Salva uma nova tarefa no banco de dados.

    Args:
        title: Título ou descrição da tarefa.
        user_id: Identificador do usuário (chat_id do Telegram ou ID fixo do CLI).
        due_date: Data/hora no formato 'YYYY-MM-DD HH:MM'. Opcional.
        priority: Prioridade da tarefa — 'low', 'medium' ou 'high'.

    Returns:
        String confirmando o salvamento ou descrevendo o erro.
    """
    db = SessionLocal()
    try:
        # Tenta converter a string de data para um objeto datetime
        # Aceita dois formatos: com hora ou só a data
        parsed_date = None
        if due_date and isinstance(due_date, str) and due_date.strip():
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    parsed_date = datetime.strptime(due_date.strip(), fmt)
                    break  # para no primeiro formato que funcionar
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
        print(f"[ERRO save_task] {e}")
        return f"Erro ao salvar tarefa: {str(e)}"
    finally:
        db.close()


def create_reminder(message: str, user_id: str, remind_at: str) -> str:
    """
    Cria um lembrete para ser disparado em um horário específico.

    Args:
        message: Texto que será enviado ao usuário no momento do lembrete.
        user_id: Identificador do usuário (chat_id do Telegram ou ID fixo do CLI).
        remind_at: Data/hora no formato 'YYYY-MM-DD HH:MM'.

    Returns:
        String confirmando a criação ou descrevendo o erro.
    """
    db = SessionLocal()
    try:
        parsed_date = datetime.strptime(remind_at.strip(), "%Y-%m-%d %H:%M")

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
        print(f"[ERRO create_reminder] {e}")
        return f"Erro ao criar lembrete: {str(e)}"
    finally:
        db.close()


def list_tasks(user_id: str, filter_date: str = None) -> str:
    """
    Lista as tarefas pendentes do usuário.

    Args:
        user_id: Identificador do usuário (chat_id do Telegram ou ID fixo do CLI).
        filter_date: Filtra tarefas de uma data específica no formato 'YYYY-MM-DD'. Opcional.

    Returns:
        String com a lista de tarefas ou mensagem de lista vazia.
    """
    db = SessionLocal()
    try:
        query = db.query(Task).filter(
            Task.user_id == user_id,
            Task.status == "pending"
        )

        # Aplica filtro de data se fornecido
        if filter_date and isinstance(filter_date, str) and filter_date.strip():
            try:
                date = datetime.strptime(filter_date.strip(), "%Y-%m-%d")
                query = query.filter(
                    Task.due_date >= date.replace(hour=0, minute=0),
                    Task.due_date <= date.replace(hour=23, minute=59)
                )
            except ValueError:
                pass  # ignora filtro inválido e retorna todas

        # Ordena por data de vencimento, colocando tarefas sem data no final
        tasks = query.order_by(Task.due_date.asc().nullslast()).all()

        if not tasks:
            return "Nenhuma tarefa pendente encontrada."

        linhas = [f"Você tem {len(tasks)} tarefa(s) pendente(s):"]
        for task in tasks:
            prazo = (
                f" — {task.due_date.strftime('%d/%m/%Y às %H:%M')}"
                if task.due_date
                else " — sem prazo definido"
            )
            prioridade = f" [{task.priority}]" if task.priority != "medium" else ""
            linhas.append(f"• {task.title}{prazo}{prioridade}")

        return "\n".join(linhas)

    except Exception as e:
        print(f"[ERRO list_tasks] {e}")
        return f"Erro ao listar tarefas: {str(e)}"
    finally:
        db.close()


def complete_task(title: str, user_id: str) -> str:
    """
    Marca uma tarefa como concluída buscando pelo título.

    Args:
        title: Título ou trecho do título da tarefa a ser concluída.
        user_id: Identificador do usuário (chat_id do Telegram ou ID fixo do CLI).

    Returns:
        String confirmando a conclusão ou informando que não foi encontrada.
    """
    db = SessionLocal()
    try:
        # Busca por correspondência parcial, ignorando maiúsculas/minúsculas
        task = db.query(Task).filter(
            Task.user_id == user_id,
            Task.status == "pending",
            Task.title.ilike(f"%{title}%")
        ).first()

        if not task:
            return f"Nenhuma tarefa pendente encontrada com '{title}'."

        task.status = "done"
        task.updated_at = datetime.utcnow()
        db.commit()

        return f"Tarefa '{task.title}' marcada como concluída! ✅"

    except Exception as e:
        db.rollback()
        print(f"[ERRO complete_task] {e}")
        return f"Erro ao concluir tarefa: {str(e)}"
    finally:
        db.close()


# ============================================================
# MAPA DE TOOLS
# Relaciona o nome da tool (string que o modelo usa) com
# a função Python correspondente. Usado pelo agente para
# saber qual função chamar após o modelo decidir.
# ============================================================

TOOLS_MAP: dict[str, callable] = {
    "save_task": save_task,
    "create_reminder": create_reminder,
    "list_tasks": list_tasks,
    "complete_task": complete_task,
}


# ============================================================
# SCHEMA DAS TOOLS
# Descrição formal das tools no formato JSON Schema que o Groq
# (e outros LLMs compatíveis com OpenAI) entendem.
#
# O modelo lê esse schema para saber:
#   - Quando chamar cada tool (description)
#   - Quais argumentos passar (parameters)
#   - Quais argumentos são obrigatórios (required)
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
                        "description": "Data e hora no formato 'YYYY-MM-DD HH:MM'. Null se não informado."
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
                        "description": "Data e hora exata no formato 'YYYY-MM-DD HH:MM'."
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
            "name": "complete_task",
            "description": (
                "Marca uma tarefa como concluída. "
                "Use quando o usuário disser que já fez algo, "
                "que pode riscar uma tarefa ou que terminou alguma atividade."
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