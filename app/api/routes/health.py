"""
Router de health check.
"""

from fastapi import APIRouter

from app.schemas.telegram import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health():
    """Verifica se a API está respondendo."""
    return HealthResponse(status="ok", version="2.0")
