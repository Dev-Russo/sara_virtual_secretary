## from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from app.agent.tools import save_task, create_reminder, list_tasks, complete_task
from app.agent.prompts import get_system_prompt
from app.db.database import SessionLocal
from app.models.conversation import ConversationHistory
from dotenv import load_dotenv
import os

load_dotenv()

USER_ID = os.getenv("USER_ID", "5511999999999")

def get_agent_executor():
    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        temperature=0.3,
        api_key=os.getenv("GROQ_API_KEY")
    )

    tools = [save_task, create_reminder, list_tasks, complete_task]

    prompt = ChatPromptTemplate.from_messages([
        ("system", get_system_prompt(USER_ID)),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])

    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=False)

def carregar_historico(user_id: str, limite: int = 20):
    db = SessionLocal()
    try:
        registros = db.query(ConversationHistory).filter(
            ConversationHistory.user_id == user_id
        ).order_by(ConversationHistory.created_at.desc()).limit(limite).all()

        registros.reverse()
        mensagens = []
        for r in registros:
            if r.role == "user":
                mensagens.append(HumanMessage(content=r.content))
            elif r.role == "assistant":
                mensagens.append(AIMessage(content=r.content))
        return mensagens
    finally:
        db.close()

def salvar_historico(user_id: str, role: str, content: str):
    db = SessionLocal()
    try:
        registro = ConversationHistory(
            user_id=user_id,
            role=role,
            content=content
        )
        db.add(registro)
        db.commit()
    finally:
        db.close()

def chat(mensagem: str) -> str:
    executor = get_agent_executor()
    historico = carregar_historico(USER_ID)

    try:
        resultado = executor.invoke({
            "input": mensagem,
            "chat_history": historico
        })
        resposta = resultado["output"]
    except Exception as e:
        resposta = f"Desculpe, tive um problema ao processar sua mensagem. Tente novamente. ({str(e)})"

    salvar_historico(USER_ID, "user", mensagem)
    salvar_historico(USER_ID, "assistant", resposta)

    return resposta