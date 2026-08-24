# Production package handoff

`GET /workflow/{workflow_id}/pacote-producao` is available only after `audio_concluido`. It returns
portable IDs, URLs and metadata—not host filesystem paths or audio bytes.

The package includes:

- workflow, Ideas Hub idea/script IDs and production profile;
- immutable canonical script blocks;
- every Ideas Hub `visual_queries` item, including provenance and preferred gameplay timestamps;
- source block URLs plus checksum, format, resolved seed, voice/generation profile and deterministic
  final-narration offsets;
- final narration URL, SHA-256, size, duration, 48 kHz mono format and mastering metrics;
- Faster-Whisper text/word timestamps, provider/model/language and the final audio SHA-256 it
  describes.

Example fragment:

```json
{
  "roteiro": {
    "blocos": [{
      "ordem": 0,
      "tipo": "intro",
      "conteudo": "Geralt aprende o primeiro sinal.",
      "visual_queries": [{
        "id": "f40bb007-b32f-4dd8-9dd5-516568a49284",
        "order": 1,
        "intent": "gameplay_event",
        "query": "Geralt learns Aard",
        "narration_excerpt": "aprende o primeiro sinal",
        "importance": "high",
        "source_note_id": null,
        "preferred_session_id": null,
        "preferred_start_seconds": 120.0,
        "preferred_end_seconds": 145.0,
        "desired_duration_seconds": 12.0,
        "notes": null
      }]
    }]
  },
  "audio": {
    "url": "/workflow/<id>/audio",
    "duracao_segundos": 42.5,
    "sha256": "...",
    "sample_rate": 48000,
    "channels": 1,
    "byte_size": 4080044,
    "mastering_config": {"version": "transparent_gain_v1"},
    "mastering_metrics": {"status": "pass"},
    "blocos": [{
      "narration_start_seconds": 0.0,
      "narration_end_seconds": 18.24,
      "resolved_seed": 123456789
    }]
  },
  "transcricao": {
    "audio_sha256": "...",
    "provider": "faster-whisper",
    "model": "small",
    "language": "pt",
    "palavras_timestamps": []
  }
}
```

## Time semantics

Do not conflate these domains:

1. `conteudo` is canonical editorial script text.
2. `AudioBloco.synthesis_text` is a pronunciation-adjusted derivative (not copied into this compact
   package today).
3. `preferred_start_seconds`/`preferred_end_seconds` are gameplay/source-media time.
4. `narration_start_seconds`/`narration_end_seconds` are deterministic block offsets in the final
   narration WAV.

There are no VisualQuery-to-narration bindings yet. Stage 13 will produce Narration Timeline / Voice
Alignment using the final audio identity, exact block ranges, canonical/synthesis text and word
timestamps. Consumers must not fabricate narration timestamps from gameplay preferences.

Old scripts without `visual_queries` serialize `visual_queries: []`.
