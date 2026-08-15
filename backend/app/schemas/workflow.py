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
