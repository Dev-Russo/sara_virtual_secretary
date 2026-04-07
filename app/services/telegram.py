"""
Serviço de comunicação com o Telegram.

Responsável por encapsular toda a lógica de envio de mensagens
via Telegram Bot API, incluindo tratamento de erros e divisão
de mensagens longas.
"""

import logging
import os
from telegram import Bot
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


async def enviar_mensagem(chat_id: str, texto: str) -> bool:
    """
    Envia uma mensagem simples para um chat do Telegram.

    Args:
        chat_id: Identificador do chat (geralmente o ID do usuário).
        texto: Texto da mensagem a ser enviada.

    Returns:
        True se a mensagem foi enviada com sucesso, False caso contrário.
    """
    try:
        await bot.send_message(chat_id=chat_id, text=texto)
        return True

    except TelegramError as e:
        logger.warning(f"Falha ao enviar mensagem para {chat_id}: {e}")
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


async def enviar_briefing(chat_id: str, tarefas: list[str]) -> bool:
    """
    Envia o briefing diário com as tarefas do dia.

    Args:
        chat_id: Identificador do chat.
        tarefas: Lista de tarefas formatadas (ex: ['09:00 - Estudar Python']).

    Returns:
        True se o briefing foi enviado com sucesso.
    """
    if not tarefas:
        return True  # Sem tarefas, não envia nada

    linhas = "\n".join(f"• {t}" for t in tarefas)
    texto = (
        f"☀️ *Briefing do Dia*\n\n"
        f"Você tem {len(tarefas)} tarefa(s) para hoje:\n\n"
        f"{linhas}\n\n"
        f"Bom trabalho! 💪"
    )

    return await enviar_mensagem_longa(chat_id, texto)
