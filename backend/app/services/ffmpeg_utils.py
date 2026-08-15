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
