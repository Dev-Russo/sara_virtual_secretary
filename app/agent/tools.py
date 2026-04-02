from langchain.tools import tool
from app.db.database import SessionLocal
from app.models.task import Task
from app.models.reminder import Reminder
from datetime import datetime
import pytz

USER_ID = "5511999999999"

def get_db():
    return SessionLocal()

@tool
def save_task(title: str, due_date: str = None, priority: str = "medium") -> str:
    """Salva uma nova tarefa no banco de dados. Use quando o usuário mencionar algo que precisa fazer,
    uma obrigação, um compromisso ou qualquer coisa que não pode esquecer.
    due_date deve estar no formato 'YYYY-MM-DD HH:MM' ou None se não informado.
    priority pode ser 'low', 'medium' ou 'high'."""
    db = get_db()
    try:
        parsed_date = None
        if due_date and isinstance(due_date, str) and due_date.strip():
            try:
                parsed_date = datetime.strptime(due_date.strip(), "%Y-%m-%d %H:%M")
            except ValueError:
                try:
                    parsed_date = datetime.strptime(due_date.strip(), "%Y-%m-%d")
                except ValueError:
                    pass

        task = Task(
            user_id=USER_ID,
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
        return f"Erro ao salvar tarefa: {str(e)}"
    finally:
        db.close()

@tool
def create_reminder(message: str, remind_at: str) -> str:
    """Cria um lembrete para ser disparado em um horário específico. Use quando o usuário pedir
    para ser lembrado de algo em um horário ou data específica.
    remind_at deve estar no formato 'YYYY-MM-DD HH:MM'."""
    db = get_db()
    try:
        parsed_date = datetime.strptime(remind_at, "%Y-%m-%d %H:%M")

        reminder = Reminder(
            user_id=USER_ID,
            message=message,
            remind_at=parsed_date,
            sent=False
        )
        db.add(reminder)
        db.commit()

        return f"Lembrete criado para {parsed_date.strftime('%d/%m/%Y às %H:%M')}: '{message}'"
    except Exception as e:
        db.rollback()
        return f"Erro ao criar lembrete: {str(e)}"
    finally:
        db.close()

@tool
def list_tasks(filter_date: str = None) -> str:
    """Lista as tarefas pendentes do usuário. Use quando o usuário perguntar o que tem para fazer,
    quais são suas tarefas, compromissos do dia, etc.
    filter_date opcional no formato 'YYYY-MM-DD' para filtrar por data específica."""
    db = get_db()
    try:
        query = db.query(Task).filter(
            Task.user_id == USER_ID,
            Task.status == "pending"
        )

        if filter_date:
            try:
                date = datetime.strptime(filter_date, "%Y-%m-%d")
                query = query.filter(
                    Task.due_date >= date.replace(hour=0, minute=0),
                    Task.due_date <= date.replace(hour=23, minute=59)
                )
            except ValueError:
                pass

        tasks = query.order_by(Task.due_date.asc().nullslast()).all()

        if not tasks:
            return "Nenhuma tarefa pendente encontrada."

        resultado = f"Você tem {len(tasks)} tarefa(s) pendente(s):\n"
        for t in tasks:
            prazo = f" — {t.due_date.strftime('%d/%m/%Y às %H:%M')}" if t.due_date else " — sem prazo definido"
            prioridade = f" [{t.priority}]" if t.priority != "medium" else ""
            resultado += f"• {t.title}{prazo}{prioridade}\n"

        return resultado
    except Exception as e:
        return f"Erro ao listar tarefas: {str(e)}"
    finally:
        db.close()

@tool
def complete_task(title: str) -> str:
    """Marca uma tarefa como concluída. Use quando o usuário disser que já fez algo,
    que pode riscar uma tarefa ou que terminou alguma atividade.
    Busca a tarefa pelo título ou parte dele."""
    db = get_db()
    try:
        task = db.query(Task).filter(
            Task.user_id == USER_ID,
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
        return f"Erro ao concluir tarefa: {str(e)}"
    finally:
        db.close()