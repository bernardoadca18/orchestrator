"""Entrypoint da API do orchestrator."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import health, workflow
from app.core.config import settings

app = FastAPI(title="orchestrator", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["health"])
app.include_router(workflow.router, prefix="/workflow", tags=["workflow"])
