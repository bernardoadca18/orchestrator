#!/usr/bin/env bash
set -Eeuo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
run_id="${RANDOM}-$$"
db_container="orchestrator-migration-test-db-${run_id}"
network_name="orchestrator-migration-test-net-${run_id}"
backend_image="orchestrator-migration-test-backend:${run_id}"

cleanup() {
    docker rm -f "${db_container}" >/dev/null 2>&1 || true
    docker network rm "${network_name}" >/dev/null 2>&1 || true
    docker image rm "${backend_image}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create "${network_name}" >/dev/null
docker run --detach \
    --name "${db_container}" \
    --network "${network_name}" \
    --tmpfs /var/lib/postgresql/data:rw \
    --env POSTGRES_USER=orchestrator_test \
    --env POSTGRES_PASSWORD=orchestrator_test \
    --env POSTGRES_DB=orchestrator_test \
    pgvector/pgvector:pg16 >/dev/null

for _ in $(seq 1 60); do
    if docker exec "${db_container}" pg_isready \
        --username orchestrator_test \
        --dbname orchestrator_test >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
docker exec "${db_container}" pg_isready \
    --username orchestrator_test \
    --dbname orchestrator_test >/dev/null

docker build --quiet --tag "${backend_image}" "${repo_dir}" >/dev/null
database_url="postgresql+asyncpg://orchestrator_test:orchestrator_test@${db_container}:5432/orchestrator_test"
docker run --rm \
    --network "${network_name}" \
    --env "DATABASE_URL=${database_url}" \
    --env PYTHONPATH=/app \
    "${backend_image}" \
    alembic upgrade head

revision="$(docker exec "${db_container}" psql -U orchestrator_test -d orchestrator_test -Atc \
    "SELECT version_num FROM alembic_version;")"
column_count="$(docker exec "${db_container}" psql -U orchestrator_test -d orchestrator_test -Atc \
    "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='audio_blocos' AND column_name IN ('text_normalizer_version','text_normalizer_identity','initial_resolved_seed','retry_seed_strategy','generation_attempt_history','current_take_id','requested_take_id','voice_chunking_identity','pronunciation_replacements','normalized_text');")"
test "${revision}" = "011"
test "${column_count}" = "10"
take_table_count="$(docker exec "${db_container}" psql -U orchestrator_test -d orchestrator_test -Atc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='audio_bloco_takes';")"
test "${take_table_count}" = "1"

echo "Fresh Orchestrator migrations passed: 001 -> 011"
