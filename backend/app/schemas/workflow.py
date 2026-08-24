"""Schemas Pydantic — contratos de entrada/saída da API."""

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field, computed_field


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
    synthesis_text: str | None
    voice_profile: str | None
    generation_profile: str | None
    resolved_seed: int | None
    generation_attempt: int
    duracao_segundos: float | None
    audio_sha256: str | None
    audio_sample_rate: int | None
    audio_channels: int | None
    audio_byte_size: int | None
    narration_start_seconds: float | None
    narration_end_seconds: float | None
    erro_mensagem: str | None


class WorkflowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    ideia_id: uuid.UUID
    roteiro_id: uuid.UUID
    perfil_producao: str | None
    status: str
    audio_final_path: str | None = Field(default=None, exclude=True)
    duracao_audio_segundos: float | None
    audio_phase: str
    audio_final_sha256: str | None
    audio_final_sample_rate: int | None
    audio_final_channels: int | None
    audio_final_byte_size: int | None
    mastering_config: dict | None
    mastering_metrics: dict | None
    erro_mensagem: str | None
    tentativas: int
    criado_em: dt.datetime
    atualizado_em: dt.datetime

    @computed_field
    @property
    def audio_available(self) -> bool:
        return bool(self.audio_final_path and self.status == "audio_concluido")


class WorkflowDetailOut(WorkflowOut):
    blocos: list[AudioBlocoOut] = Field(default_factory=list)

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


# ============================================================================
# Pacote de Produção Visual (Fase 6)
# ============================================================================


class BlocoAudioPacoteOut(BaseModel):
    """Bloco de áudio no pacote de produção."""

    id: uuid.UUID
    ordem: int
    tipo: str
    audio_url: str | None
    duracao_segundos: float | None
    sha256: str | None
    sample_rate: int | None
    channels: int | None
    byte_size: int | None
    narration_start_seconds: float | None
    narration_end_seconds: float | None
    voice_profile: str | None
    generation_profile: str | None
    resolved_seed: int | None
    generation_attempt: int


class AudioPacoteOut(BaseModel):
    """Seção de áudio do pacote de produção."""

    url: str
    duracao_segundos: float | None
    sha256: str | None
    sample_rate: int | None
    channels: int | None
    byte_size: int | None
    mastering_config: dict | None
    mastering_metrics: dict | None
    blocos: list[BlocoAudioPacoteOut] = Field(default_factory=list)


class PalavraTimestampPacote(BaseModel):
    """Timestamp de uma palavra na transcrição."""

    palavra: str
    inicio: float
    fim: float


class TranscricaoPacoteOut(BaseModel):
    """Seção de transcrição do pacote de produção."""

    texto_completo: str
    palavras_timestamps: list[PalavraTimestampPacote] = Field(default_factory=list)
    audio_sha256: str | None
    provider: str | None
    model: str | None
    language: str | None


class VisualQueryPacote(BaseModel):
    """Structured gameplay-time request preserved exactly from Ideas Hub."""

    id: uuid.UUID
    order: int
    intent: str
    query: str
    narration_excerpt: str
    importance: str
    source_note_id: uuid.UUID | None = None
    preferred_session_id: uuid.UUID | None = None
    preferred_start_seconds: float | None = None
    preferred_end_seconds: float | None = None
    desired_duration_seconds: float | None = None
    notes: str | None = None


class BlocoRoteiroPacote(BaseModel):
    """Bloco do roteiro no pacote de produção."""

    ordem: int
    tipo: str
    conteudo: str
    visual_queries: list[VisualQueryPacote] = Field(default_factory=list)


class RoteiroPacoteOut(BaseModel):
    """Seção de roteiro do pacote de produção."""

    titulo: str
    duracao_total_segundos: int
    blocos: list[BlocoRoteiroPacote] = Field(default_factory=list)


class MetadataPacoteOut(BaseModel):
    """Metadata do pacote de produção."""

    criado_em: dt.datetime
    atualizado_em: dt.datetime
    tentativas: int


class PacoteProducaoOut(BaseModel):
    """
    Payload consolidado para handoff ao serviço de produção visual.

    Este endpoint agrega todos os dados necessários para o serviço visual
    iniciar a produção do vídeo: roteiro, áudio, transcrição e metadata.
    """

    workflow_id: uuid.UUID
    ideia_id: uuid.UUID
    roteiro_id: uuid.UUID

    status: str
    duracao_audio_segundos: float | None

    perfil_producao: str | None

    roteiro: RoteiroPacoteOut
    audio: AudioPacoteOut
    transcricao: TranscricaoPacoteOut | None = None
    metadata: MetadataPacoteOut
