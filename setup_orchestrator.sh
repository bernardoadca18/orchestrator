#!/usr/bin/env bash
# ============================================================================
# setup_orchestrator.sh
#
# Gera a estrutura completa do microsserviço "orchestrator": fila de
# produção (Celery + Redis), geração de áudio bloco a bloco a partir dos
# roteiros aprovados no ideas-hub, e frontend mínimo para acompanhar/disparar
# o fluxo. Rode este script na pasta raiz onde quer criar o projeto
# (ex: workspaces/neovids/).
#
# Uso:
#   chmod +x setup_orchestrator.sh
#   ./setup_orchestrator.sh
# ============================================================================
set -euo pipefail

PROJECT_DIR="orchestrator"

if [ -d "$PROJECT_DIR" ]; then
  echo "ERRO: a pasta '$PROJECT_DIR' já existe. Remova-a ou rode em outro diretório."
  exit 1
fi

echo "==> Criando estrutura de pastas em ./$PROJECT_DIR ..."

mkdir -p "$PROJECT_DIR"/backend/app/{models,schemas,repositories,clients,services,tasks,api/routers,core}
mkdir -p "$PROJECT_DIR"/backend/alembic/versions
mkdir -p "$PROJECT_DIR"/backend/tests
mkdir -p "$PROJECT_DIR"/frontend/src/{api,pages,components}
mkdir -p "$PROJECT_DIR"/data/audio

cd "$PROJECT_DIR"

for pkg in app app/models app/schemas app/repositories app/clients app/services \
           app/tasks app/api app/api/routers app/core tests; do
  touch "backend/$pkg/__init__.py"
done

# ============================================================================
# BACKEND
# ============================================================================

# ----------------------------------------------------------------------------
# core/config.py
# ----------------------------------------------------------------------------
cat > backend/app/core/config.py <<'EOF'
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
EOF

# ----------------------------------------------------------------------------
# core/database.py
# ----------------------------------------------------------------------------
cat > backend/app/core/database.py <<'EOF'
"""Engine e sessão assíncrona (SQLAlchemy 2.0 + asyncpg)."""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)

AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    """Dependency do FastAPI para injeção de sessão por request."""
    async with AsyncSessionLocal() as session:
        yield session
EOF

# ----------------------------------------------------------------------------
# core/celery_app.py
# ----------------------------------------------------------------------------
cat > backend/app/core/celery_app.py <<'EOF'
"""
App Celery do orchestrator.

A fila "audio_producao" roda com --concurrency=1 propositalmente: o
voice-workshop só processa uma narração por vez (lock de GPU), então
paralelizar workers aqui só geraria erro 500 do lado do voice-workshop.
Mesmo padrão já usado no pipeline de vídeo para filas de GPU.
"""
from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "orchestrator",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.audio_tasks"],
)

celery_app.conf.update(
    task_routes={"app.tasks.audio_tasks.*": {"queue": "audio_producao"}},
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)
EOF

# ----------------------------------------------------------------------------
# models/workflow.py
# ----------------------------------------------------------------------------
cat > backend/app/models/workflow.py <<'EOF'
"""
Modelos SQLAlchemy do orchestrator.

Design: o orchestrator NÃO tem FK de banco para o ideas-hub (são bancos
de serviços diferentes — FK cross-database não existe fisicamente).
`ideia_id` e `roteiro_id` são apenas UUIDs de referência externa.

`roteiro_snapshot` guarda uma cópia imutável da `estrutura` do roteiro
no momento em que ele entrou na fila — garante reprodutibilidade mesmo
se o roteiro for editado no ideas-hub depois, e evita uma tabela extra
de cache (o snapshot já cumpre esse papel).

`AudioBloco` materializa cada bloco narrativo como linha própria (em vez
de só ler do JSONB) porque cada bloco tem ciclo de vida e status próprios
durante a geração — é um compromisso de denormalização deliberado e
localizado, não um problema de modelagem.
"""
import datetime as dt
import uuid

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class WorkflowExecucao(Base):
    __tablename__ = "workflow_execucoes"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ideia_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    roteiro_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, index=True)
    roteiro_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)

    status: Mapped[str] = mapped_column(
        PgEnum(
            "agendada", "gerando_audio", "audio_concluido", "erro", "cancelada",
            name="status_workflow", create_type=False,
        ),
        nullable=False,
        default="agendada",
    )
    audio_final_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duracao_audio_segundos: Mapped[float | None] = mapped_column(Float, nullable=True)
    erro_mensagem: Mapped[str | None] = mapped_column(Text, nullable=True)
    tentativas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_tentativas: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    criado_em: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    blocos: Mapped[list["AudioBloco"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan", order_by="AudioBloco.ordem"
    )


class AudioBloco(Base):
    __tablename__ = "audio_blocos"
    __table_args__ = (UniqueConstraint("workflow_execucao_id", "ordem", name="uq_audio_bloco_ordem"),)

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_execucao_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("workflow_execucoes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordem: Mapped[int] = mapped_column(Integer, nullable=False)
    tipo: Mapped[str] = mapped_column(String(50), nullable=False)  # introducao | desenvolvimento | conclusao | ...
    texto: Mapped[str] = mapped_column(Text, nullable=False)

    job_id_voz: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        PgEnum("pendente", "gerando", "concluido", "erro", name="status_audio_bloco", create_type=False),
        nullable=False,
        default="pendente",
    )
    audio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duracao_segundos: Mapped[float | None] = mapped_column(Float, nullable=True)
    erro_mensagem: Mapped[str | None] = mapped_column(Text, nullable=True)

    criado_em: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    workflow: Mapped["WorkflowExecucao"] = relationship(back_populates="blocos")
EOF

# ----------------------------------------------------------------------------
# schemas/workflow.py
# ----------------------------------------------------------------------------
cat > backend/app/schemas/workflow.py <<'EOF'
"""Schemas Pydantic — contratos de entrada/saída da API."""
import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict


class RoteiroDisponivelOut(BaseModel):
    """Roteiro aprovado no ideas-hub, ainda sem workflow (ou reexibido)."""
    id: uuid.UUID
    ideia_id: uuid.UUID
    versao: int
    titulo: str
    duracao_total_segundos: int
    num_blocos: int


class WorkflowCreateIn(BaseModel):
    roteiro_id: uuid.UUID


class AudioBlocoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    ordem: int
    tipo: str
    status: str
    duracao_segundos: float | None
    erro_mensagem: str | None


class WorkflowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    ideia_id: uuid.UUID
    roteiro_id: uuid.UUID
    status: str
    audio_final_path: str | None
    duracao_audio_segundos: float | None
    erro_mensagem: str | None
    tentativas: int
    criado_em: dt.datetime
    atualizado_em: dt.datetime


class WorkflowDetailOut(WorkflowOut):
    blocos: list[AudioBlocoOut] = []
EOF

# ----------------------------------------------------------------------------
# repositories/workflow_repository.py
# ----------------------------------------------------------------------------
cat > backend/app/repositories/workflow_repository.py <<'EOF'
"""Camada de acesso a dados para WorkflowExecucao e AudioBloco."""
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.workflow import AudioBloco, WorkflowExecucao


class WorkflowRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def criar(self, ideia_id: uuid.UUID, roteiro_id: uuid.UUID, roteiro_snapshot: dict) -> WorkflowExecucao:
        wf = WorkflowExecucao(ideia_id=ideia_id, roteiro_id=roteiro_id, roteiro_snapshot=roteiro_snapshot)
        self.session.add(wf)
        await self.session.commit()
        await self.session.refresh(wf)
        return wf

    async def obter_ativo_por_roteiro(self, roteiro_id: uuid.UUID) -> WorkflowExecucao | None:
        stmt = select(WorkflowExecucao).where(
            WorkflowExecucao.roteiro_id == roteiro_id,
            WorkflowExecucao.status.in_(["agendada", "gerando_audio", "audio_concluido"]),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def obter_por_id(self, workflow_id: uuid.UUID, com_blocos: bool = False) -> WorkflowExecucao | None:
        stmt = select(WorkflowExecucao).where(WorkflowExecucao.id == workflow_id)
        if com_blocos:
            stmt = stmt.options(selectinload(WorkflowExecucao.blocos))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def listar(self, status: str | None = None) -> Sequence[WorkflowExecucao]:
        stmt = select(WorkflowExecucao).order_by(WorkflowExecucao.criado_em.desc())
        if status:
            stmt = stmt.where(WorkflowExecucao.status == status)
        return (await self.session.execute(stmt)).scalars().all()

    async def atualizar_status(self, workflow_id: uuid.UUID, status: str, **campos) -> None:
        wf = await self.obter_por_id(workflow_id)
        if wf is None:
            return
        wf.status = status
        for chave, valor in campos.items():
            setattr(wf, chave, valor)
        await self.session.commit()

    async def incrementar_tentativa(self, workflow_id: uuid.UUID) -> None:
        wf = await self.obter_por_id(workflow_id)
        if wf is None:
            return
        wf.tentativas += 1
        wf.status = "agendada"
        wf.erro_mensagem = None
        await self.session.commit()

    # --- AudioBloco ----------------------------------------------------------

    async def upsert_bloco(
        self, workflow_id: uuid.UUID, ordem: int, tipo: str, texto: str
    ) -> AudioBloco:
        stmt = select(AudioBloco).where(
            AudioBloco.workflow_execucao_id == workflow_id, AudioBloco.ordem == ordem
        )
        bloco = (await self.session.execute(stmt)).scalar_one_or_none()
        if bloco is None:
            bloco = AudioBloco(workflow_execucao_id=workflow_id, ordem=ordem, tipo=tipo, texto=texto)
            self.session.add(bloco)
        else:
            bloco.status = "pendente"
            bloco.erro_mensagem = None
        await self.session.commit()
        await self.session.refresh(bloco)
        return bloco

    async def atualizar_bloco(self, bloco_id: uuid.UUID, **campos) -> None:
        stmt = select(AudioBloco).where(AudioBloco.id == bloco_id)
        bloco = (await self.session.execute(stmt)).scalar_one_or_none()
        if bloco is None:
            return
        for chave, valor in campos.items():
            setattr(bloco, chave, valor)
        await self.session.commit()
EOF

# ----------------------------------------------------------------------------
# clients/ideas_hub_client.py
# ----------------------------------------------------------------------------
cat > backend/app/clients/ideas_hub_client.py <<'EOF'
"""Client HTTP para o serviço ideas-hub (somente leitura, sem acoplamento de DB)."""
import uuid

import httpx

from app.core.config import settings


class IdeasHubClient:
    def __init__(self):
        self._base_url = settings.ideas_hub_api_url
        self._timeout = settings.ideas_hub_api_timeout

    async def listar_roteiros_aprovados(self) -> list[dict]:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
            resp = await client.get("/roteiros/", params={"status": "aprovado"})
            resp.raise_for_status()
            return resp.json()

    async def obter_roteiro(self, roteiro_id: uuid.UUID) -> dict:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
            resp = await client.get(f"/roteiros/{roteiro_id}")
            resp.raise_for_status()
            return resp.json()


ideas_hub_client = IdeasHubClient()
EOF

# ----------------------------------------------------------------------------
# clients/voice_client.py
# ----------------------------------------------------------------------------
cat > backend/app/clients/voice_client.py <<'EOF'
"""
Client HTTP para o voice-workshop (síntese + conversão de voz).

Ver API_REFERENCE.md do voice-workshop: POST /narrar retorna job_id
imediatamente; status é consultado via polling em GET /jobs/{job_id};
áudio é baixado uma única vez via GET /jobs/{job_id}/audio (o arquivo é
removido do lado do voice-workshop após o download).
"""
import asyncio

import httpx

from app.core.config import settings


class VoiceClientError(RuntimeError):
    pass


class VoiceClientTimeoutError(VoiceClientError):
    pass


class VoiceClient:
    def __init__(self):
        self._base_url = settings.voice_api_url

    async def iniciar_narracao(self, texto: str, run_name: str | None = None, qualidade: str | None = None) -> str:
        payload = {
            "texto": texto,
            "run_name": run_name or settings.voice_run_name,
            "qualidade": qualidade or settings.voice_qualidade_padrao,
        }
        async with httpx.AsyncClient(base_url=self._base_url, timeout=30) as client:
            resp = await client.post("/narrar", json=payload)
            if resp.status_code == 400:
                raise VoiceClientError(f"Requisição rejeitada pelo voice-workshop: {resp.json().get('detail')}")
            resp.raise_for_status()
            return resp.json()["job_id"]

    async def aguardar_conclusao(self, job_id: str) -> dict:
        """Faz polling até status=concluido ou status=erro, respeitando o timeout configurado."""
        elapsed = 0
        async with httpx.AsyncClient(base_url=self._base_url, timeout=30) as client:
            while elapsed < settings.voice_job_timeout_seconds:
                resp = await client.get(f"/jobs/{job_id}")
                resp.raise_for_status()
                dados = resp.json()
                if dados["status"] == "concluido":
                    return dados
                if dados["status"] == "erro":
                    raise VoiceClientError(dados.get("erro") or "Job de narração falhou sem detalhe")
                await asyncio.sleep(settings.voice_poll_interval_seconds)
                elapsed += settings.voice_poll_interval_seconds
        raise VoiceClientTimeoutError(f"Job {job_id} excedeu timeout de {settings.voice_job_timeout_seconds}s")

    async def baixar_audio(self, job_id: str) -> bytes:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=60) as client:
            resp = await client.get(f"/jobs/{job_id}/audio")
            resp.raise_for_status()
            return resp.content


voice_client = VoiceClient()
EOF

# ----------------------------------------------------------------------------
# services/ffmpeg_utils.py
# ----------------------------------------------------------------------------
cat > backend/app/services/ffmpeg_utils.py <<'EOF'
"""Utilitários de linha de comando ffmpeg — geração de silêncio, concat e duração."""
import asyncio
import json
from pathlib import Path


async def _rodar(*args: str) -> None:
    processo = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await processo.communicate()
    if processo.returncode != 0:
        raise RuntimeError(f"Comando falhou ({' '.join(args)}): {stderr.decode(errors='ignore')}")


async def gerar_silencio(destino: Path, duracao_ms: int, sample_rate: int = 22050) -> None:
    duracao_s = max(duracao_ms, 0) / 1000
    await _rodar(
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl=mono",
        "-t", f"{duracao_s}", "-ar", str(sample_rate), "-ac", "1", str(destino),
    )


async def concatenar_com_silencio(caminhos_blocos: list[Path], silencio_ms: int, destino: Path) -> Path:
    """Concatena os áudios dos blocos, inserindo um silêncio calibrável entre cada um."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    lista_path = destino.parent / f"{destino.stem}_concat_list.txt"

    silencio_path: Path | None = None
    if silencio_ms > 0 and len(caminhos_blocos) > 1:
        silencio_path = destino.parent / "_silencio.wav"
        await gerar_silencio(silencio_path, silencio_ms)

    with lista_path.open("w") as f:
        for i, caminho in enumerate(caminhos_blocos):
            f.write(f"file '{caminho.resolve()}'\n")
            if silencio_path is not None and i < len(caminhos_blocos) - 1:
                f.write(f"file '{silencio_path.resolve()}'\n")

    # Re-encode (sem -c copy) para normalizar formato entre blocos que podem
    # ter vindo com sample rate/canais levemente diferentes do voice-workshop.
    await _rodar(
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lista_path),
        "-ar", "22050", "-ac", "1", "-c:a", "pcm_s16le", str(destino),
    )
    return destino


async def obter_duracao_segundos(caminho: Path) -> float:
    processo = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(caminho),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await processo.communicate()
    if processo.returncode != 0:
        raise RuntimeError(f"ffprobe falhou: {stderr.decode(errors='ignore')}")
    dados = json.loads(stdout)
    return float(dados["format"]["duration"])
EOF

# ----------------------------------------------------------------------------
# services/audio_service.py
# ----------------------------------------------------------------------------
cat > backend/app/services/audio_service.py <<'EOF'
"""
Serviço central: gera o áudio completo de um workflow, bloco a bloco,
a partir do roteiro estruturado (BlocoNarrativo-compatível), usando o
voice-workshop, e concatena tudo num único arquivo final.

Chamado pela task Celery (app/tasks/audio_tasks.py) — mantido separado
para poder ser testado sem depender do broker.
"""
import logging
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.voice_client import VoiceClientError, voice_client
from app.core.config import settings
from app.repositories.workflow_repository import WorkflowRepository
from app.services.ffmpeg_utils import concatenar_com_silencio, obter_duracao_segundos

logger = logging.getLogger(__name__)


async def processar_workflow(session: AsyncSession, workflow_id: uuid.UUID) -> None:
    repo = WorkflowRepository(session)
    wf = await repo.obter_por_id(workflow_id, com_blocos=True)
    if wf is None:
        logger.warning("Workflow %s não encontrado — abortando task", workflow_id)
        return

    await repo.atualizar_status(wf.id, "gerando_audio")

    blocos_estrutura = wf.roteiro_snapshot.get("blocos", [])
    if not blocos_estrutura:
        await repo.atualizar_status(wf.id, "erro", erro_mensagem="Roteiro sem blocos narrativos")
        return

    pasta_workflow = Path(settings.audio_storage_path) / str(wf.id)
    pasta_workflow.mkdir(parents=True, exist_ok=True)

    caminhos_blocos: list[Path] = []

    for ordem, bloco_estrutura in enumerate(blocos_estrutura):
        texto = bloco_estrutura["conteudo"]
        tipo = bloco_estrutura.get("tipo", f"bloco_{ordem}")
        bloco = await repo.upsert_bloco(wf.id, ordem=ordem, tipo=tipo, texto=texto)
        await repo.atualizar_bloco(bloco.id, status="gerando")

        try:
            job_id = await voice_client.iniciar_narracao(texto=texto)
            await repo.atualizar_bloco(bloco.id, job_id_voz=job_id)

            resultado = await voice_client.aguardar_conclusao(job_id)
            audio_bytes = await voice_client.baixar_audio(job_id)

            caminho_bloco = pasta_workflow / f"bloco_{ordem:02d}.wav"
            caminho_bloco.write_bytes(audio_bytes)
            duracao = await obter_duracao_segundos(caminho_bloco)

            await repo.atualizar_bloco(
                bloco.id, status="concluido", audio_path=str(caminho_bloco), duracao_segundos=duracao
            )
            caminhos_blocos.append(caminho_bloco)

        except VoiceClientError as exc:
            logger.exception("Falha ao gerar bloco %s do workflow %s", ordem, wf.id)
            await repo.atualizar_bloco(bloco.id, status="erro", erro_mensagem=str(exc))
            await repo.atualizar_status(
                wf.id, "erro", erro_mensagem=f"Falha no bloco {ordem} ({tipo}): {exc}"
            )
            return

    caminho_final = pasta_workflow / "audio_final.wav"
    await concatenar_com_silencio(caminhos_blocos, settings.silencio_entre_blocos_ms, caminho_final)
    duracao_total = await obter_duracao_segundos(caminho_final)

    await repo.atualizar_status(
        wf.id,
        "audio_concluido",
        audio_final_path=str(caminho_final),
        duracao_audio_segundos=duracao_total,
    )
    logger.info("Workflow %s concluído — áudio final: %s (%.1fs)", wf.id, caminho_final, duracao_total)
EOF

# ----------------------------------------------------------------------------
# tasks/audio_tasks.py
# ----------------------------------------------------------------------------
cat > backend/app/tasks/audio_tasks.py <<'EOF'
"""
Task Celery que dispara a geração de áudio de um workflow.

Roda na fila "audio_producao" com --concurrency=1 (ver core/celery_app.py)
para respeitar o lock de GPU do voice-workshop. `asyncio.run` cria um
event loop novo por execução de task — seguro aqui porque cada task abre
sua própria sessão de banco via AsyncSessionLocal.
"""
import asyncio
import uuid

from app.core.celery_app import celery_app
from app.core.database import AsyncSessionLocal
from app.services.audio_service import processar_workflow


@celery_app.task(name="app.tasks.audio_tasks.gerar_audio_workflow", bind=True, max_retries=0)
def gerar_audio_workflow(self, workflow_id: str) -> None:
    asyncio.run(_processar(workflow_id))


async def _processar(workflow_id: str) -> None:
    async with AsyncSessionLocal() as session:
        await processar_workflow(session, uuid.UUID(workflow_id))
EOF

# ----------------------------------------------------------------------------
# api/routers/health.py
# ----------------------------------------------------------------------------
cat > backend/app/api/routers/health.py <<'EOF'
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}
EOF

# ----------------------------------------------------------------------------
# api/routers/workflow.py
# ----------------------------------------------------------------------------
cat > backend/app/api/routers/workflow.py <<'EOF'
"""
Endpoints de workflow: listar roteiros aprovados disponíveis no ideas-hub,
enfileirar geração de áudio, acompanhar progresso e baixar o resultado.
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.ideas_hub_client import ideas_hub_client
from app.core.config import settings
from app.core.database import get_db
from app.repositories.workflow_repository import WorkflowRepository
from app.schemas.workflow import RoteiroDisponivelOut, WorkflowCreateIn, WorkflowDetailOut, WorkflowOut
from app.tasks.audio_tasks import gerar_audio_workflow

router = APIRouter()


@router.get("/roteiros-disponiveis", response_model=list[RoteiroDisponivelOut])
async def listar_roteiros_disponiveis():
    """Proxy read-only para os roteiros aprovados no ideas-hub."""
    roteiros = await ideas_hub_client.listar_roteiros_aprovados()
    return [
        RoteiroDisponivelOut(
            id=r["id"],
            ideia_id=r["ideia_id"],
            versao=r["versao"],
            titulo=r["estrutura"]["titulo"],
            duracao_total_segundos=r["estrutura"]["duracao_total_segundos"],
            num_blocos=len(r["estrutura"]["blocos"]),
        )
        for r in roteiros
    ]


@router.post("", response_model=WorkflowOut, status_code=201)
async def criar_workflow(
    payload: WorkflowCreateIn, session: Annotated[AsyncSession, Depends(get_db)]
):
    repo = WorkflowRepository(session)

    existente = await repo.obter_ativo_por_roteiro(payload.roteiro_id)
    if existente is not None:
        raise HTTPException(409, f"Já existe um workflow ativo para este roteiro (id={existente.id})")

    roteiro = await ideas_hub_client.obter_roteiro(payload.roteiro_id)
    if roteiro["status"] != "aprovado":
        raise HTTPException(400, "Roteiro precisa estar aprovado no ideas-hub para entrar na fila de produção")

    wf = await repo.criar(
        ideia_id=uuid.UUID(roteiro["ideia_id"]),
        roteiro_id=payload.roteiro_id,
        roteiro_snapshot=roteiro["estrutura"],
    )
    gerar_audio_workflow.delay(str(wf.id))
    return wf


@router.get("", response_model=list[WorkflowOut])
async def listar_workflows(session: Annotated[AsyncSession, Depends(get_db)], status: str | None = None):
    repo = WorkflowRepository(session)
    return await repo.listar(status=status)


@router.get("/{workflow_id}", response_model=WorkflowDetailOut)
async def obter_workflow(workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]):
    repo = WorkflowRepository(session)
    wf = await repo.obter_por_id(workflow_id, com_blocos=True)
    if wf is None:
        raise HTTPException(404, "Workflow não encontrado")
    return wf


@router.patch("/{workflow_id}/retry", response_model=WorkflowOut)
async def repetir_workflow(workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]):
    repo = WorkflowRepository(session)
    wf = await repo.obter_por_id(workflow_id)
    if wf is None:
        raise HTTPException(404, "Workflow não encontrado")
    if wf.status != "erro":
        raise HTTPException(400, "Só é possível repetir workflows com status 'erro'")
    if wf.tentativas >= wf.max_tentativas:
        raise HTTPException(400, f"Limite de tentativas atingido ({wf.max_tentativas})")

    await repo.incrementar_tentativa(wf.id)
    gerar_audio_workflow.delay(str(wf.id))
    return await repo.obter_por_id(wf.id)


@router.patch("/{workflow_id}/cancelar", response_model=WorkflowOut)
async def cancelar_workflow(workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]):
    repo = WorkflowRepository(session)
    wf = await repo.obter_por_id(workflow_id)
    if wf is None:
        raise HTTPException(404, "Workflow não encontrado")
    await repo.atualizar_status(wf.id, "cancelada")
    return await repo.obter_por_id(wf.id)


@router.get("/{workflow_id}/audio")
async def baixar_audio_final(workflow_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_db)]):
    repo = WorkflowRepository(session)
    wf = await repo.obter_por_id(workflow_id)
    if wf is None or not wf.audio_final_path:
        raise HTTPException(404, "Áudio final ainda não disponível para este workflow")
    return FileResponse(wf.audio_final_path, media_type="audio/wav", filename=f"{workflow_id}.wav")
EOF

# ----------------------------------------------------------------------------
# api/main.py
# ----------------------------------------------------------------------------
cat > backend/app/main.py <<'EOF'
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
EOF

# ----------------------------------------------------------------------------
# Alembic
# ----------------------------------------------------------------------------
cat > backend/alembic.ini <<'EOF'
[alembic]
script_location = alembic
prepend_sys_path = .
sqlalchemy.url = driver://user:pass@localhost/dbname

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console
qualname =

[logger_sqlalchemy]
level = WARNING
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
EOF

cat > backend/alembic/env.py <<'EOF'
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.models.workflow import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
config.set_main_option("sqlalchemy.url", settings.database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url, target_metadata=target_metadata,
        literal_binds=True, dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = create_async_engine(settings.database_url, poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
EOF

cat > backend/alembic/script.py.mako <<'EOF'
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
EOF

cat > backend/alembic/versions/001_schema_inicial.py <<'EOF'
"""schema inicial: workflow_execucoes, audio_blocos

Revision ID: 001
Revises:
Create Date: 2026-08-15

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    status_workflow = postgresql.ENUM(
        "agendada", "gerando_audio", "audio_concluido", "erro", "cancelada", name="status_workflow"
    )
    status_audio_bloco = postgresql.ENUM("pendente", "gerando", "concluido", "erro", name="status_audio_bloco")

    bind = op.get_bind()
    status_workflow.create(bind, checkfirst=True)
    status_audio_bloco.create(bind, checkfirst=True)

    op.create_table(
        "workflow_execucoes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ideia_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("roteiro_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("roteiro_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("status", status_workflow, nullable=False, server_default="agendada"),
        sa.Column("audio_final_path", sa.String(500), nullable=True),
        sa.Column("duracao_audio_segundos", sa.Float, nullable=True),
        sa.Column("erro_mensagem", sa.Text, nullable=True),
        sa.Column("tentativas", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_tentativas", sa.Integer, nullable=False, server_default="3"),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_workflow_execucoes_ideia_id", "workflow_execucoes", ["ideia_id"])
    op.create_index("ix_workflow_execucoes_roteiro_id", "workflow_execucoes", ["roteiro_id"])

    op.create_table(
        "audio_blocos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workflow_execucao_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_execucoes.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("ordem", sa.Integer, nullable=False),
        sa.Column("tipo", sa.String(50), nullable=False),
        sa.Column("texto", sa.Text, nullable=False),
        sa.Column("job_id_voz", sa.String(64), nullable=True),
        sa.Column("status", status_audio_bloco, nullable=False, server_default="pendente"),
        sa.Column("audio_path", sa.String(500), nullable=True),
        sa.Column("duracao_segundos", sa.Float, nullable=True),
        sa.Column("erro_mensagem", sa.Text, nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("workflow_execucao_id", "ordem", name="uq_audio_bloco_ordem"),
    )
    op.create_index("ix_audio_blocos_workflow_execucao_id", "audio_blocos", ["workflow_execucao_id"])


def downgrade() -> None:
    op.drop_table("audio_blocos")
    op.drop_table("workflow_execucoes")
    op.execute("DROP TYPE IF EXISTS status_audio_bloco")
    op.execute("DROP TYPE IF EXISTS status_workflow")
EOF

# ----------------------------------------------------------------------------
# requirements / Dockerfile (backend + worker)
# ----------------------------------------------------------------------------
cat > backend/requirements.txt <<'EOF'
fastapi==0.115.*
uvicorn[standard]==0.32.*
sqlalchemy[asyncio]==2.0.*
asyncpg==0.30.*
alembic==1.14.*
pydantic==2.9.*
pydantic-settings==2.6.*
httpx==0.27.*
celery[redis]==5.4.*
redis==5.2.*
EOF

cat > backend/Dockerfile <<'EOF'
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
EOF

# ============================================================================
# FRONTEND (Vite + React + TS — mínimo funcional)
# ============================================================================

cat > frontend/package.json <<'EOF'
{
  "name": "orchestrator-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite --host",
    "build": "tsc -b && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.3",
    "typescript": "^5.6.3",
    "vite": "^5.4.10"
  }
}
EOF

cat > frontend/tsconfig.json <<'EOF'
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true
  },
  "include": ["src"]
}
EOF

cat > frontend/vite.config.ts <<'EOF'
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { port: 5174 },
});
EOF

cat > frontend/index.html <<'EOF'
<!doctype html>
<html lang="pt-BR">
  <head>
    <meta charset="UTF-8" />
    <title>Orchestrator — Fila de Produção</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
EOF

cat > frontend/src/main.tsx <<'EOF'
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
EOF

cat > frontend/src/api/client.ts <<'EOF'
const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8011";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `Erro ${resp.status}`);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json();
}

export interface RoteiroDisponivel {
  id: string;
  ideia_id: string;
  versao: number;
  titulo: string;
  duracao_total_segundos: number;
  num_blocos: number;
}

export interface AudioBloco {
  id: string;
  ordem: number;
  tipo: string;
  status: string;
  duracao_segundos: number | null;
  erro_mensagem: string | null;
}

export interface Workflow {
  id: string;
  ideia_id: string;
  roteiro_id: string;
  status: string;
  audio_final_path: string | null;
  duracao_audio_segundos: number | null;
  erro_mensagem: string | null;
  tentativas: number;
  criado_em: string;
  atualizado_em: string;
  blocos?: AudioBloco[];
}

export const api = {
  listarRoteirosDisponiveis: () => request<RoteiroDisponivel[]>("/workflow/roteiros-disponiveis"),
  listarWorkflows: () => request<Workflow[]>("/workflow"),
  obterWorkflow: (id: string) => request<Workflow>(`/workflow/${id}`),
  criarWorkflow: (roteiro_id: string) =>
    request<Workflow>("/workflow", { method: "POST", body: JSON.stringify({ roteiro_id }) }),
  repetirWorkflow: (id: string) => request<Workflow>(`/workflow/${id}/retry`, { method: "PATCH" }),
  cancelarWorkflow: (id: string) => request<Workflow>(`/workflow/${id}/cancelar`, { method: "PATCH" }),
  urlAudio: (id: string) => `${BASE_URL}/workflow/${id}/audio`,
};
EOF

cat > frontend/src/App.tsx <<'EOF'
import { useEffect, useState } from "react";
import { api, RoteiroDisponivel, Workflow } from "./api/client";

export default function App() {
  const [roteiros, setRoteiros] = useState<RoteiroDisponivel[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [erro, setErro] = useState<string | null>(null);

  async function carregar() {
    try {
      const [r, w] = await Promise.all([api.listarRoteirosDisponiveis(), api.listarWorkflows()]);
      setRoteiros(r);
      setWorkflows(w);
      setErro(null);
    } catch (e) {
      setErro((e as Error).message);
    }
  }

  useEffect(() => {
    carregar();
    const interval = setInterval(carregar, 5000); // poll simples de progresso
    return () => clearInterval(interval);
  }, []);

  const idsEmWorkflow = new Set(workflows.map((w) => w.roteiro_id));

  return (
    <div style={{ fontFamily: "sans-serif", maxWidth: 900, margin: "2rem auto", padding: "0 1rem" }}>
      <h1>Fila de Produção</h1>
      {erro && <p style={{ color: "crimson" }}>Erro: {erro}</p>}

      <h2>Roteiros aprovados (ideas-hub)</h2>
      <ul>
        {roteiros
          .filter((r) => !idsEmWorkflow.has(r.id))
          .map((r) => (
            <li key={r.id} style={{ marginBottom: 8 }}>
              <strong>{r.titulo}</strong> — {r.num_blocos} blocos, ~{r.duracao_total_segundos}s{" "}
              <button onClick={() => api.criarWorkflow(r.id).then(carregar)}>Gerar áudio</button>
            </li>
          ))}
        {roteiros.filter((r) => !idsEmWorkflow.has(r.id)).length === 0 && <p>Nenhum roteiro novo disponível.</p>}
      </ul>

      <h2>Workflows</h2>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ textAlign: "left", borderBottom: "1px solid #ccc" }}>
            <th>Status</th>
            <th>Duração</th>
            <th>Tentativas</th>
            <th>Ações</th>
          </tr>
        </thead>
        <tbody>
          {workflows.map((w) => (
            <tr key={w.id} style={{ borderBottom: "1px solid #eee" }}>
              <td>{w.status}{w.erro_mensagem ? ` — ${w.erro_mensagem}` : ""}</td>
              <td>{w.duracao_audio_segundos ? `${w.duracao_audio_segundos.toFixed(1)}s` : "—"}</td>
              <td>{w.tentativas}</td>
              <td>
                {w.status === "audio_concluido" && (
                  <audio controls src={api.urlAudio(w.id)} style={{ height: 30 }} />
                )}
                {w.status === "erro" && (
                  <button onClick={() => api.repetirWorkflow(w.id).then(carregar)}>Repetir</button>
                )}
                {(w.status === "agendada" || w.status === "gerando_audio") && (
                  <button onClick={() => api.cancelarWorkflow(w.id).then(carregar)}>Cancelar</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
EOF

cat > frontend/Dockerfile <<'EOF'
FROM node:20-slim
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
EXPOSE 5174
CMD ["npm", "run", "dev"]
EOF

# ============================================================================
# Infra raiz (docker-compose, .env, README)
# ============================================================================

cat > docker-compose.yml <<'EOF'
services:
  db:
    image: pgvector/pgvector:pg16
    container_name: orchestrator-db
    restart: unless-stopped
    environment:
      POSTGRES_USER: orchestrator
      POSTGRES_PASSWORD: orchestrator
      POSTGRES_DB: orchestrator
    ports:
      - "5434:5432"
    volumes:
      - orchestrator-db-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U orchestrator"]
      interval: 5s
      timeout: 5s
      retries: 10

  redis:
    image: redis:7-alpine
    container_name: orchestrator-redis
    restart: unless-stopped
    ports:
      - "6380:6379"

  api:
    build: ./backend
    container_name: orchestrator-api
    restart: unless-stopped
    env_file: .env
    environment:
      DATABASE_URL: postgresql+asyncpg://orchestrator:orchestrator@db:5432/orchestrator
      REDIS_URL: redis://redis:6379/0
    ports:
      - "8011:8000"
    depends_on:
      db:
        condition: service_healthy
    volumes:
      - ./backend:/app
      - ./data/audio:/data/audio

  worker:
    build: ./backend
    container_name: orchestrator-worker
    restart: unless-stopped
    env_file: .env
    environment:
      DATABASE_URL: postgresql+asyncpg://orchestrator:orchestrator@db:5432/orchestrator
      REDIS_URL: redis://redis:6379/0
    command: celery -A app.core.celery_app.celery_app worker -Q audio_producao --concurrency=1 -l info
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_started
    volumes:
      - ./backend:/app
      - ./data/audio:/data/audio

  frontend:
    build: ./frontend
    container_name: orchestrator-frontend
    restart: unless-stopped
    env_file: .env
    ports:
      - "5174:5174"
    depends_on:
      - api
    volumes:
      - ./frontend:/app
      - /app/node_modules

volumes:
  orchestrator-db-data:
    name: orchestrator-db-data
EOF

cat > .env.example <<'EOF'
# --- Banco / fila ---
DATABASE_URL=postgresql+asyncpg://orchestrator:orchestrator@localhost:5434/orchestrator
REDIS_URL=redis://localhost:6380/0

# --- Serviços integrados ---
IDEAS_HUB_API_URL=http://localhost:8010
IDEAS_HUB_API_TIMEOUT=30

VOICE_API_URL=http://localhost:9000
VOICE_RUN_NAME=minha_voz_v1
VOICE_QUALIDADE_PADRAO=final
VOICE_POLL_INTERVAL_SECONDS=5
VOICE_JOB_TIMEOUT_SECONDS=900

# --- Armazenamento ---
AUDIO_STORAGE_PATH=/data/audio

# --- Parâmetros calibráveis ---
SILENCIO_ENTRE_BLOCOS_MS=500
WORKFLOW_MAX_TENTATIVAS=3

# --- API ---
API_PORT=8001
CORS_ORIGINS=http://localhost:5174

# --- Frontend ---
VITE_API_URL=http://localhost:8011
EOF

cat > .gitignore <<'EOF'
__pycache__/
*.pyc
.env
.venv/
venv/
node_modules/
dist/
data/audio/*
!data/audio/.gitkeep
EOF

touch data/audio/.gitkeep

cat > README.md <<'EOF'
# orchestrator

Microsserviço responsável por orquestrar o fluxo de produção entre o
ideas-hub (roteiros aprovados) e as próximas etapas do pipeline
(produção visual, ainda não implementada).

Nesta primeira etapa, o orchestrator:
- Lista roteiros aprovados vindos do ideas-hub
- Enfileira (Celery + Redis) a geração de áudio bloco a bloco via
  voice-workshop, respeitando o lock de GPU (fila com concurrency=1)
- Concatena os blocos com silêncio calibrável entre eles
- Persiste o áudio final + roteiro (snapshot) num registro de workflow

## Subir localmente

```bash
cp .env.example .env
docker compose up -d db redis
cd backend && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
alembic upgrade head
cd ..
docker compose up -d --build
```

Frontend em http://localhost:5174, API em http://localhost:8011.
EOF

echo ""
echo "==> Estrutura criada com sucesso em ./$PROJECT_DIR"
echo "Próximos passos:"
echo "  1. cd $PROJECT_DIR"
echo "  2. cp .env.example .env"
echo "  3. Confirme as portas/URLs do ideas-hub e do voice-workshop no .env"
echo "  4. docker compose up -d db redis"
echo "  5. cd backend && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
echo "  6. alembic upgrade head"
echo "  7. cd .. && docker compose up -d --build"
