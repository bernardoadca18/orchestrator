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
