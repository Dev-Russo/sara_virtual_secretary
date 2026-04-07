"""
Schemas para validação de payloads da API.
"""

from pydantic import BaseModel


class TelegramMessage(BaseModel):
    """
    Schema simplificado para uma mensagem do Telegram.
    """

    chat_id: str
    text: str
    first_name: str = "Usuário"


class TelegramUpdate(BaseModel):
    """
    Schema para o update recebido do Telegram.
    """

    update_id: int
    message: dict | None = None
    edited_message: dict | None = None


class HealthResponse(BaseModel):
    """
    Schema para o health check.
    """

    status: str
    version: str


class WebhookStatus(BaseModel):
    """
    Schema para resposta do webhook.
    """

    status: str
