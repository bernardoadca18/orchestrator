"""Serviço de transcrição de áudio usando Faster-Whisper com timestamps por palavra."""
import logging
from pathlib import Path

from faster_whisper import WhisperModel

from app.core.config import settings

logger = logging.getLogger(__name__)


def transcrever_audio(caminho_audio: Path) -> tuple[str, list[dict]]:
    """
    Transcreve áudio usando Faster-Whisper com timestamps por palavra.

    Args:
        caminho_audio: Caminho para o arquivo de áudio WAV.

    Returns:
        Tupla (texto_completo, palavras_timestamps) onde:
        - texto_completo: Texto transcrito completo.
        - palavras_timestamps: Lista de dicts com {'palavra': str, 'inicio': float, 'fim': float}.

    Raises:
        RuntimeError: Se a transcrição falhar.
    """
    try:
        # Usar modelo small para balancear velocidade e qualidade
        model = WhisperModel("small", device="cpu", compute_type="int8")

        segments, _ = model.transcribe(str(caminho_audio), word_timestamps=True)

        texto_completo = []
        palavras_timestamps = []

        for segment in segments:
            texto_completo.append(segment.text)

            if segment.words:
                for word in segment.words:
                    palavras_timestamps.append({
                        "palavra": word.word.strip(),
                        "inicio": round(word.start, 3),
                        "fim": round(word.end, 3),
                    })

        return " ".join(texto_completo), palavras_timestamps

    except Exception as exc:
        logger.exception("Falha na transcrição do áudio %s", caminho_audio)
        raise RuntimeError(f"Erro na transcrição: {exc}") from exc
