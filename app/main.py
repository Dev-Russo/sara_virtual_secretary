from contextlib import asynccontextmanager
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from app.api.routes.telegram import router as telegram_router
from app.api.routes.health import router as health_router
from app.scheduler.jobs import iniciar_scheduler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa o scheduler junto com a aplicação."""
    iniciar_scheduler(scheduler)
    scheduler.start()
    logger.info("🚀 Scheduler iniciado")
    yield
    scheduler.shutdown()
    logger.info("🛑 Scheduler encerrado")


app = FastAPI(title="Sara Virtual Secretary", version="2.0", lifespan=lifespan)

app.include_router(telegram_router)
app.include_router(health_router)
