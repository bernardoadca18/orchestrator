"""Schemas Pydantic — contratos de entrada/saída da API."""
import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, computed_field


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
    perfil_producao: str | None
    status: str
    audio_final_path: str | None
    duracao_audio_segundos: float | None
    erro_mensagem: str | None
    tentativas: int
    criado_em: dt.datetime
    atualizado_em: dt.datetime


class WorkflowDetailOut(WorkflowOut):
    blocos: list[AudioBlocoOut] = []

    @computed_field
    @property
    def total_blocos(self) -> int:
        return len(self.blocos)

    @computed_field
    @property
    def blocos_concluidos(self) -> int:
        return sum(1 for b in self.blocos if b.status == "concluido")

    @computed_field
    @property
    def blocos_pendentes(self) -> int:
        return sum(1 for b in self.blocos if b.status == "pendente")

    @computed_field
    @property
    def blocos_gerando(self) -> int:
        return sum(1 for b in self.blocos if b.status == "gerando")

    @computed_field
    @property
    def blocos_erro(self) -> int:
        return sum(1 for b in self.blocos if b.status == "erro")
