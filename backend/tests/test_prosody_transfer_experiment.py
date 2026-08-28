from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

from app.services.prosody_transfer_experiment import run_prosody_transfer_experiment


def test_experiment_releases_heavy_providers_sequentially_and_preserves_reference(
    tmp_path: Path, write_tone
) -> None:
    target = write_tone(tmp_path / "target.wav", duration=1.0, frequency=180)
    guide = write_tone(tmp_path / "guide.wav", duration=1.0, frequency=240)
    original_reference = target.read_bytes()
    events: list[str] = []

    class Guide:
        name = "fake-guide"

        def generate_guide(self, **kwargs):
            events.append("guide_generate")
            return SimpleNamespace(
                path=guide,
                provider=self.name,
                provider_identity="guide-v1",
                request_identity="guide-request-v1",
            )

        def release(self):
            events.append("guide_release")

    class Converter:
        name = "fake-vc"

        def transfer_timbre(self, *, guide, target_reference, output):
            events.append("vc_convert")
            shutil.copyfile(guide, output)
            return SimpleNamespace(
                path=output,
                provider=self.name,
                provider_identity="vc-v1",
                request_identity="vc-request-v1",
            )

        def release(self):
            events.append("vc_release")

    result = run_prosody_transfer_experiment(
        text="Texto completo.",
        language="pt-BR",
        style="professional documentary",
        target_reference=target,
        output=tmp_path / "converted.wav",
        guide_provider=Guide(),
        conversion_provider=Converter(),
    )
    assert events == ["guide_generate", "guide_release", "vc_convert", "vc_release"]
    assert result.manifest["production_promoted"] is False
    assert result.converted.duration_seconds == result.guide.duration_seconds
    assert target.read_bytes() == original_reference
