"""Configuração central do orchestrator, via variáveis de ambiente."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Banco de dados / fila ---------------------------------------------
    database_url: str = "postgresql+asyncpg://orchestrator:orchestrator@localhost:5433/orchestrator"
    redis_url: str = "redis://localhost:6380/0"

    # --- Serviços integrados -------------------------------------------------
    ideas_hub_api_url: str = "http://localhost:8010"
    ideas_hub_api_timeout: int = 30

    voice_api_url: str = "http://localhost:9000"
    voice_run_name: str = "minha_voz_v1"
    voice_qualidade_padrao: str = "final"  # "draft" | "final"
    voice_poll_interval_seconds: int = 5
    voice_job_timeout_seconds: int = 900

    # --- Armazenamento de áudio ----------------------------------------------
    audio_storage_path: str = "/data/audio"

    # --- Parâmetros calibráveis do fluxo -------------------------------------
    # Silêncio inserido entre blocos narrativos na concatenação final.
    silencio_entre_blocos_ms: int = 500
    workflow_max_tentativas: int = 3

    # --- API -----------------------------------------------------------------
    api_port: int = 8001
    cors_origins: str = "http://localhost:5174"


settings = Settings()
