from groq import Groq
from app.agent.tools import save_task, create_reminder, list_tasks, complete_task
from app.agent.prompts import get_system_prompt
from app.db.database import SessionLocal
from app.models.conversation import ConversationHistory
from dotenv import load_dotenv
import json, os

load_dotenv()

USER_ID = os.getenv("USER_ID", "5511999999999")

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# Mapa de tools disponíveis
TOOLS_MAP = {
    "save_task": save_task,
    "create_reminder": create_reminder,
    "list_tasks": list_tasks,
    "complete_task": complete_task,
}

# Definição das tools no formato que o Groq entende
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "save_task",
            "description": "Salva uma nova tarefa. Use quando o usuário mencionar algo que precisa fazer, uma obrigação ou compromisso.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Título da tarefa"},
                    "due_date": {"type": "string", "description": "Data no formato YYYY-MM-DD HH:MM ou null"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high"], "description": "Prioridade da tarefa"}
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_reminder",
            "description": "Cria um lembrete para um horário específico. Use quando o usuário pedir para ser lembrado de algo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Texto do lembrete"},
                    "remind_at": {"type": "string", "description": "Data e hora no formato YYYY-MM-DD HH:MM"}
                },
                "required": ["message", "remind_at"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "Lista tarefas pendentes. Use quando o usuário perguntar o que tem para fazer ou pedir para listar tarefas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filter_date": {"type": "string", "description": "Filtrar por data no formato YYYY-MM-DD. Opcional."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Marca uma tarefa como concluída. Use quando o usuário disser que já fez algo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Título ou parte do título da tarefa"}
                },
                "required": ["title"]
            }
        }
    }
]

def carregar_historico(user_id: str, limite: int = 10):
    db = SessionLocal()
    try:
        registros = db.query(ConversationHistory).filter(
            ConversationHistory.user_id == user_id
        ).order_by(ConversationHistory.created_at.desc()).limit(limite).all()
        registros.reverse()
        return [{"role": r.role, "content": str(r.content)} for r in registros]
    except Exception as e:
        print(f"[ERRO carregar_historico] {e}")
        return []
    finally:
        db.close()

def salvar_historico(user_id: str, role: str, content: str):
    db = SessionLocal()
    try:
        registro = ConversationHistory(user_id=user_id, role=role, content=content)
        db.add(registro)
        db.commit()
    except Exception as e:
        print(f"[ERRO salvar_historico] {e}")
        db.rollback()
    finally:
        db.close()

def executar_tool(nome: str, argumentos: dict) -> str:
    tool_fn = TOOLS_MAP.get(nome)
    if not tool_fn:
        return f"Tool '{nome}' não encontrada."
    try:
        return tool_fn.invoke(argumentos)
    except Exception as e:
        print(f"[ERRO tool {nome}] {e}")
        return f"Erro ao executar {nome}: {str(e)}"

def chat(mensagem: str) -> str:
    historico = carregar_historico(USER_ID)

    messages = [
        {"role": "system", "content": get_system_prompt(USER_ID)},
        *historico,
        {"role": "user", "content": mensagem}
    ]

    try:
        # Primeira chamada — modelo decide se usa tool ou responde direto
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            tools=TOOLS_SCHEMA,
            tool_choice="auto",
            temperature=0.3,
            max_tokens=1024
        )

        msg = response.choices[0].message

        # Se o modelo quer chamar uma tool
        if msg.tool_calls:
            messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ]})

            # Executa cada tool e adiciona o resultado
            for tool_call in msg.tool_calls:
                nome = tool_call.function.name
                args = json.loads(tool_call.function.arguments)
                print(f"[Tool chamada] {nome}({args})")
                resultado = executar_tool(nome, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": resultado
                })

            # Segunda chamada — modelo formula resposta final com o resultado da tool
            response2 = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                temperature=0.3,
                max_tokens=1024
            )
            resposta = response2.choices[0].message.content

        else:
            # Modelo respondeu direto sem usar tool
            resposta = msg.content

    except Exception as e:
        import traceback
        traceback.print_exc()
        resposta = f"Desculpe, tive um problema. Tente novamente. ({str(e)})"

    salvar_historico(USER_ID, "user", mensagem)
    salvar_historico(USER_ID, "assistant", resposta)

    return resposta