"""
Serviço de comunicação com o Telegram.

Responsável por encapsular toda a lógica de envio de mensagens
via Telegram Bot API, incluindo tratamento de erros e divisão
de mensagens longas.
"""

import logging
import os
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

# Limite de caracteres do Telegram
MAX_MESSAGE_LENGTH = 4096

# Lê o token diretamente da variável de ambiente
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN não está configurado")

# Inicializa o bot uma única vez
bot = Bot(token=TELEGRAM_BOT_TOKEN)

# Estado em memória da revisão de tarefas (chat_id → {message_id, tasks})
# tasks: {task_id_str → {title, horario, done}}
_revisao_state: dict[str, dict] = {}


async def enviar_mensagem(chat_id: str, texto: str) -> bool:
    """
    Envia uma mensagem simples para um chat do Telegram.
    Tenta até 3 vezes com intervalo de 2s em caso de falha.

    Returns:
        True se a mensagem foi enviada com sucesso, False caso contrário.
    """
    import asyncio
    tentativas = 3
    for tentativa in range(1, tentativas + 1):
        try:
            await bot.send_message(chat_id=chat_id, text=texto)
            if tentativa > 1:
                logger.info(f"Mensagem enviada para {chat_id} na tentativa {tentativa}.")
            return True
        except TelegramError as e:
            logger.warning(f"Tentativa {tentativa}/{tentativas} falhou para {chat_id}: {e}")
            if tentativa < tentativas:
                await asyncio.sleep(2)

    logger.error(f"Falha definitiva ao enviar mensagem para {chat_id} após {tentativas} tentativas.")
    return False


async def enviar_mensagem_longa(chat_id: str, texto: str) -> bool:
    """
    Envia uma mensagem para o Telegram, dividindo automaticamente
    se o texto ultrapassar o limite de 4096 caracteres.

    Args:
        chat_id: Identificador do chat.
        texto: Texto completo da mensagem.

    Returns:
        True se todas as partes foram enviadas, False caso contrário.
    """
    if len(texto) <= MAX_MESSAGE_LENGTH:
        return await enviar_mensagem(chat_id, texto)

    # Divide o texto em partes
    sucesso = True
    partes = [
        texto[i : i + MAX_MESSAGE_LENGTH]
        for i in range(0, len(texto), MAX_MESSAGE_LENGTH)
    ]

    for i, parte in enumerate(partes, start=1):
        # Adiciona indicador de continuação se houver mais partes
        if len(partes) > 1 and i < len(partes):
            parte = parte + "\n\n(continuando...)"

        enviado = await enviar_mensagem(chat_id, parte)
        if not enviado:
            sucesso = False
            logger.warning(
                f"Falha ao enviar parte {i}/{len(partes)} para {chat_id}"
            )

    return sucesso


async def enviar_lembrete(chat_id: str, mensagem: str) -> bool:
    """
    Envia um lembrete formatado para o usuário.

    Args:
        chat_id: Identificador do chat.
        mensagem: Texto do lembrete.

    Returns:
        True se o lembrete foi enviado com sucesso.
    """
    texto = f"⏰ *Lembrete*\n\n{mensagem}"
    try:
        await bot.send_message(
            chat_id=chat_id, text=texto, parse_mode="Markdown"
        )
        return True

    except TelegramError as e:
        logger.warning(f"Falha ao enviar lembrete para {chat_id}: {e}")
        # Tenta enviar sem formatação Markdown
        try:
            texto_sem_md = f"⏰ Lembrete\n\n{mensagem}"
            await bot.send_message(chat_id=chat_id, text=texto_sem_md)
            return True
        except TelegramError as e2:
            logger.warning(
                f"Falha ao enviar lembrete (sem formatação) para {chat_id}: {e2}"
            )
            return False


async def enviar_inicio_planejamento(chat_id: str) -> bool:
    """
    Abre a sessão de planejamento noturno com uma mensagem conversacional.

    Returns:
        True se enviado com sucesso.
    """
    texto = "E aí, como foi o dia?"
    return await enviar_mensagem(chat_id, texto)


async def enviar_revisao_tarefas(chat_id: str, tarefas: list) -> bool:
    """
    Envia mensagem com inline keyboard para revisão das tarefas do dia.
    Cada tarefa aparece como botão que o usuário toca para marcar como feita.

    Args:
        chat_id: Identificador do chat.
        tarefas: Lista de objetos Task com due_date para hoje.

    Returns:
        True se a mensagem foi enviada com sucesso.
    """
    import pytz as _pytz

    _tz = _pytz.timezone("America/Sao_Paulo")
    state_tasks: dict[str, dict] = {}
    keyboard: list[list[InlineKeyboardButton]] = []

    for tarefa in tarefas:
        task_id = str(tarefa.id)
        horario = None
        if tarefa.due_date:
            dt = tarefa.due_date
            if dt.tzinfo is None:
                dt = _pytz.utc.localize(dt).astimezone(_tz)
            else:
                dt = dt.astimezone(_tz)
            if not (dt.hour == 0 and dt.minute == 0):
                horario = dt.strftime("%H:%M")

        label = f"☐ {tarefa.title}" + (f" ({horario})" if horario else "")
        state_tasks[task_id] = {"title": tarefa.title, "horario": horario, "done": False}
        keyboard.append([InlineKeyboardButton(text=label, callback_data=f"task:{task_id}")])

    keyboard.append([InlineKeyboardButton(text="✓ Concluir revisão", callback_data="concluir_revisao")])

    try:
        msg = await bot.send_message(
            chat_id=chat_id,
            text="Revisão do dia — o que você fez hoje?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        _revisao_state[chat_id] = {"message_id": msg.message_id, "tasks": state_tasks}
        return True
    except TelegramError as e:
        logger.error(f"Erro ao enviar revisão de tarefas para {chat_id}: {e}")
        return False


async def editar_revisao_tarefas(chat_id: str, message_id: int) -> bool:
    """
    Edita a mensagem de revisão para refletir o estado atual (☐ / ✅) de cada tarefa.
    """
    state = _revisao_state.get(chat_id)
    if not state:
        return False

    keyboard: list[list[InlineKeyboardButton]] = []
    for task_id, info in state["tasks"].items():
        prefix = "✅" if info["done"] else "☐"
        label = f"{prefix} {info['title']}" + (f" ({info['horario']})" if info.get("horario") else "")
        keyboard.append([InlineKeyboardButton(text=label, callback_data=f"task:{task_id}")])

    keyboard.append([InlineKeyboardButton(text="✓ Concluir revisão", callback_data="concluir_revisao")])

    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return True
    except TelegramError as e:
        logger.error(f"Erro ao editar revisão para {chat_id}: {e}")
        return False


async def responder_callback(callback_query_id: str) -> None:
    """Confirma o callback query para remover o loading spinner do botão."""
    try:
        await bot.answer_callback_query(callback_query_id=callback_query_id)
    except TelegramError as e:
        logger.warning(f"Erro ao responder callback {callback_query_id}: {e}")


async def enviar_briefing(chat_id: str, tarefas: list[str]) -> bool:
    """
    Envia o briefing matinal em formato de tópicos.

    Args:
        chat_id: Identificador do chat.
        tarefas: Lista de tarefas formatadas (ex: ['07:00 — academia', 'reunião']).

    Returns:
        True se o briefing foi enviado com sucesso.
    """
    if not tarefas:
        texto = "Bom dia! Você está com o dia livre hoje. Aproveita ou me fala se quiser planejar algo."
    else:
        itens = "\n".join(f"• {t}" for t in tarefas)
        texto = f"Bom dia! Suas tarefas de hoje:\n\n{itens}"

    return await enviar_mensagem(chat_id, texto)
