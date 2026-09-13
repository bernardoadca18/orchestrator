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
test "${revision}" = "019"
test "${column_count}" = "10"
take_table_count="$(docker exec "${db_container}" psql -U orchestrator_test -d orchestrator_test -Atc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='audio_bloco_takes';")"
test "${take_table_count}" = "1"
take_provenance_count="$(docker exec "${db_container}" psql -U orchestrator_test -d orchestrator_test -Atc \
    "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='audio_bloco_takes' AND column_name IN ('canonical_text','normalized_text','synthesis_text','pronunciation_glossary_identity','pronunciation_replacements','text_normalizer_version','text_normalizer_identity');")"
test "${take_provenance_count}" = "7"
integrity_column_count="$(docker exec "${db_container}" psql -U orchestrator_test -d orchestrator_test -Atc \
    "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND ((table_name='workflow_execucoes' AND column_name IN ('audio_final_frame_count','audio_final_subtype','audio_integrity','narration_diagnostics')) OR (table_name='audio_blocos' AND column_name IN ('audio_frame_count','audio_subtype','artifact_integrity','pause_diagnostics')) OR (table_name='audio_bloco_takes' AND column_name IN ('audio_sample_rate','audio_channels','audio_frame_count','audio_subtype','quality_status','artifact_integrity','provider_metadata','content_quality','pause_diagnostics')));")"
test "${integrity_column_count}" = "17"
production_error_history_count="$(docker exec "${db_container}" psql -U orchestrator_test -d orchestrator_test -Atc \
    "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='workflow_execucoes' AND column_name='production_error_history';")"
test "${production_error_history_count}" = "1"
chunk_table_count="$(docker exec "${db_container}" psql -U orchestrator_test -d orchestrator_test -Atc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('audio_chunks','audio_chunk_takes');")"
test "${chunk_table_count}" = "2"
chunk_plan_column_count="$(docker exec "${db_container}" psql -U orchestrator_test -d orchestrator_test -Atc \
    "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='audio_chunks' AND column_name IN ('start_char','end_char','boundary','chunking_identity','chunking_strategy_version','voice_profile_identity','generation_profile_identity','source_job_id');")"
test "${chunk_plan_column_count}" = "8"
current_take_fk_count="$(docker exec "${db_container}" psql -U orchestrator_test -d orchestrator_test -Atc \
    "SELECT count(*) FROM information_schema.table_constraints WHERE table_schema='public' AND table_name='audio_chunks' AND constraint_name='fk_audio_chunks_current_take_id' AND constraint_type='FOREIGN KEY';")"
test "${current_take_fk_count}" = "1"
audio_edit_column_count="$(docker exec "${db_container}" psql -U orchestrator_test -d orchestrator_test -Atc \
    "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND ((table_name='workflow_execucoes' AND column_name IN ('audio_edit_revision','audio_materialized_revision')) OR (table_name='audio_blocos' AND column_name IN ('audio_edit_revision','audio_materialized_revision')) OR (table_name='audio_chunks' AND column_name='pause_after_frames'));")"
test "${audio_edit_column_count}" = "5"
audio_edit_update_table_count="$(docker exec "${db_container}" psql -U orchestrator_test -d orchestrator_test -Atc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='audio_edit_updates';")"
test "${audio_edit_update_table_count}" = "1"
chunk_edit_take_kind_count="$(docker exec "${db_container}" psql -U orchestrator_test -d orchestrator_test -Atc \
    "SELECT count(*) FROM pg_constraint WHERE conname='ck_audio_bloco_take_kind' AND pg_get_constraintdef(oid) LIKE '%chunk_edit_reassembly%';")"
test "${chunk_edit_take_kind_count}" = "1"
assembly_manifest_column_count="$(docker exec "${db_container}" psql -U orchestrator_test -d orchestrator_test -Atc \
    "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND ((table_name='audio_blocos' AND column_name IN ('assembly_manifest_identity','assembly_manifest')) OR (table_name='audio_bloco_takes' AND column_name IN ('assembly_manifest_identity','assembly_manifest')));")"
test "${assembly_manifest_column_count}" = "4"
chunk_take_provenance_count="$(docker exec "${db_container}" psql -U orchestrator_test -d orchestrator_test -Atc \
    "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='audio_chunk_takes' AND column_name IN ('audio_channels','audio_subtype','source_job_id','artifact_integrity','provider_metadata','speaker_similarity','speaker_similarity_reference_identity','speaker_similarity_policy_version','speaker_identity_status');")"
test "${chunk_take_provenance_count}" = "9"
accent_diagnostics_column_count="$(docker exec "${db_container}" psql -U orchestrator_test -d orchestrator_test -Atc \
    "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND ((table_name='audio_bloco_takes' AND column_name='accent_quality') OR (table_name='audio_chunk_takes' AND column_name IN ('accent_quality','quality_rejection_reason')));")"
test "${accent_diagnostics_column_count}" = "3"

docker run --rm \
    --network "${network_name}" \
    --env "DATABASE_URL=${database_url}" \
    --env PYTHONPATH=/app \
    "${backend_image}" \
    alembic downgrade 012
revision="$(docker exec "${db_container}" psql -U orchestrator_test -d orchestrator_test -Atc \
    "SELECT version_num FROM alembic_version;")"
test "${revision}" = "012"
integrity_column_count="$(docker exec "${db_container}" psql -U orchestrator_test -d orchestrator_test -Atc \
    "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND ((table_name='workflow_execucoes' AND column_name IN ('audio_final_frame_count','audio_final_subtype','audio_integrity','narration_diagnostics')) OR (table_name='audio_blocos' AND column_name IN ('audio_frame_count','audio_subtype','artifact_integrity','pause_diagnostics')) OR (table_name='audio_bloco_takes' AND column_name IN ('audio_sample_rate','audio_channels','audio_frame_count','audio_subtype','quality_status','artifact_integrity','provider_metadata','content_quality','pause_diagnostics')));")"
test "${integrity_column_count}" = "0"
docker exec "${db_container}" psql -U orchestrator_test -d orchestrator_test -v ON_ERROR_STOP=1 -c \
    "INSERT INTO workflow_execucoes (id, ideia_id, roteiro_id, roteiro_snapshot, status) VALUES ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002', '00000000-0000-0000-0000-000000000003', '{}'::jsonb, 'agendada'); INSERT INTO audio_blocos (id, workflow_execucao_id, ordem, tipo, texto, status) VALUES ('00000000-0000-0000-0000-000000000004', '00000000-0000-0000-0000-000000000001', 0, 'intro', 'Historical text', 'concluido'); INSERT INTO audio_bloco_takes (id, audio_bloco_id, attempt_index, kind, status, is_current) VALUES ('00000000-0000-0000-0000-000000000005', '00000000-0000-0000-0000-000000000004', 0, 'initial', 'completed', true);" >/dev/null

docker run --rm \
    --network "${network_name}" \
    --env "DATABASE_URL=${database_url}" \
    --env PYTHONPATH=/app \
    "${backend_image}" \
    alembic upgrade head
revision="$(docker exec "${db_container}" psql -U orchestrator_test -d orchestrator_test -Atc \
    "SELECT version_num FROM alembic_version;")"
test "${revision}" = "019"
historical_take_count="$(docker exec "${db_container}" psql -U orchestrator_test -d orchestrator_test -Atc \
    "SELECT count(*) FROM audio_bloco_takes WHERE id='00000000-0000-0000-0000-000000000005' AND status='completed' AND canonical_text IS NULL AND normalized_text IS NULL AND synthesis_text IS NULL AND pronunciation_glossary_identity IS NULL AND pronunciation_replacements IS NULL AND text_normalizer_version IS NULL AND text_normalizer_identity IS NULL AND audio_sample_rate IS NULL AND audio_channels IS NULL AND audio_frame_count IS NULL AND audio_subtype IS NULL AND quality_status IS NULL AND artifact_integrity IS NULL AND provider_metadata IS NULL AND content_quality IS NULL AND pause_diagnostics IS NULL;")"
test "${historical_take_count}" = "1"

echo "Fresh/reversible Orchestrator migrations passed: 001 -> 019 -> 012 -> 019"
