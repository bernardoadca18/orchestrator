#!/usr/bin/env bash
set -euo pipefail

# Development bootstrap. The canonical voice dependency is qwen3-tts-workshop;
# start it separately on port 8889 before processing workflows.
cd "$(dirname "$0")"
test -f .env || cp .env.example .env
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt -r backend/requirements-test.txt
docker compose up -d db redis
(cd backend && .venv/bin/alembic upgrade head)
echo "Orchestrator prepared. Start the stack with: docker compose up --build"
