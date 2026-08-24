"""Schemas Pydantic para transcrição de áudio."""

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict


class PalavraTimestamp(BaseModel):
    """Timestamp de uma palavra na transcrição."""

    palavra: str
    inicio: float
    fim: float


class TranscricaoOut(BaseModel):
    """Transcrição de áudio de um workflow."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workflow_execucao_id: uuid.UUID
    texto_completo: str
    palavras_timestamps: list[PalavraTimestamp]
    audio_sha256: str | None
    provider: str | None
    model: str | None
    language: str | None
    criado_em: dt.datetime
