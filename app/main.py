from fastapi import FastAPI
from app.api.routes.telegram import router as telegram_router
from app.api.routes.health import router as health_router

app = FastAPI(title="Sara Virtual Secretary", version="2.0")

app.include_router(telegram_router)
app.include_router(health_router)
