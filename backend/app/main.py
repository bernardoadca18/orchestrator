"""Entrypoint da API do orchestrator."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import dashboard, health, workflow
from app.core.config import settings
from app.core.logging_config import setup_logging

# Configurar logging estruturado antes de criar a app
setup_logging(json_format=settings.json_logging)

app = FastAPI(title="orchestrator", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["health"])
app.include_router(workflow.router, prefix="/workflow", tags=["workflow"])
app.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
