from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api.routers import operator
from app.clients.voice_client import VoiceProfileDescriptor
from app.core.config import Settings


@pytest.mark.asyncio
async def test_operator_lists_profiles_and_previews_with_requested_voice(monkeypatch):
    class FakeVoiceClient:
        def __init__(self):
            self.requests = []
            self.waited = []
            self.downloaded = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def list_voices(self):
            return [
                VoiceProfileDescriptor(
                    name=name,
                    version="1",
                    identity="a" * 64,
                    clone_mode="x_vector_only",
                    reference_id="test-reference.wav",
                )
                for name in ("bernardo_xvec_v3", "bernardo_pt_xvec_candidate_v1")
            ]

        async def create_narration(self, **request):
            self.requests.append(request)
            return SimpleNamespace(id="fake-preview")

        async def wait_for_narration(self, job_id):
            self.waited.append(job_id)
            return SimpleNamespace(id=job_id)

        async def download_audio(self, job_id):
            self.downloaded.append(job_id)
            return b"fake-wav-response"

    voice = FakeVoiceClient()
    config = Settings(_env_file=None)
    monkeypatch.setattr(operator, "settings", config)
    monkeypatch.setattr(operator, "VoiceClient", lambda settings: voice)
    app = FastAPI()
    app.include_router(operator.router, prefix="/operator")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/operator/voice-profiles")
        assert response.status_code == 200
        catalog = response.json()
        assert catalog["defaults"] == {
            "pt-BR": config.voice_profile,
            "en-US": "bernardo_en_xvec_v1",
        }
        assert len(catalog["profiles"]) == 2  # missing EN is not a global error
        assert set(catalog["profiles"][0]) == {
            "name",
            "clone_mode",
            "reference_id",
            "version",
            "identity",
        }
        for language in ("Portuguese", "English"):
            response = await client.post(
                "/operator/voice-preview",
                json={
                    "voice_profile": "bernardo_pt_xvec_candidate_v1",
                    "language": language,
                    "text": "Short editable text.",
                },
            )
            assert response.status_code == 200
            assert response.headers["content-type"] == "audio/wav"
            assert response.content == b"fake-wav-response"
            request = voice.requests[-1]
            assert request["voice_profile"] == "bernardo_pt_xvec_candidate_v1"
            assert request["generation_profile"] == "narration_v2"
            assert request["seed"] == 303
            assert request["language"] == language
            assert request["text"] == "Short editable text."
        assert voice.waited == voice.downloaded == ["fake-preview", "fake-preview"]
        missing = await client.post(
            "/operator/voice-preview",
            json={"voice_profile": "bernardo_en_xvec_v1", "language": "English", "text": "Test"},
        )
        assert missing.status_code == 409
        for text in (" " * 5, "a" * 801):
            invalid = await client.post(
                "/operator/voice-preview",
                json={"voice_profile": "bernardo_xvec_v3", "language": "Portuguese", "text": text},
            )
            assert invalid.status_code == 422
        assert len(voice.requests) == 2
