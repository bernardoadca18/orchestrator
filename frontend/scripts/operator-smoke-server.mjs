import { createServer } from "node:http";

const workflowId = "11111111-1111-4111-8111-111111111111";
const renderId = "22222222-2222-4222-8222-222222222222";
const now = "2026-09-14T12:00:00Z";
const sha = "a".repeat(64);
const fingerprint = "b".repeat(64);
const capability = (allowed, reason = null, invalidates = []) => ({ allowed, reason, invalidates });
const review = { state: "pending", current: null, latest_historical: null, current_render: null };
const summary = {
  id: workflowId, title: "GTA — Operação Smoke", script_duration_seconds: 30,
  audio_duration_seconds: 29.8, production_profile: "gta_editorial_v1",
  legacy_status: "audio_concluido", production_status: "completed", production_phase: "completed",
  created_at: now, updated_at: now, current_error_category: null, current_error_message: null,
  attention: [{ code: "human_review_pending", severity: "warning", message: "O render final corrente aguarda revisão humana." }],
  capabilities: {
    can_cancel: capability(false, "A produção não está ativa."), can_resume: capability(false, "Produção concluída."),
    can_retry: capability(false, "Não há falha."),
    can_regenerate_narration: capability(true, null, ["alignment", "visual_plan", "timeline", "render", "qa", "human_review"]),
    can_apply_audio_edits: capability(false, "Não há edições pendentes."),
    can_run_standard_qa: capability(true, null, ["qa"]), can_run_deep_qa: capability(true, null, ["qa"]),
    can_review: capability(true, null, ["human_review"]),
    can_change_music: capability(true, null, ["audio_mix_plan", "render", "qa", "human_review"]),
  },
  render: { id: renderId, status: "completed", phase: "completed", progress: 1, frames_rendered: 900, total_frames: 900, render_fingerprint: fingerprint, output_sha256: sha, updated_at: now },
  qa: { id: "33333333-3333-4333-8333-333333333333", status: "passed_with_warnings", publication_ready: true, qa_mode: "standard", request_fingerprint: "c".repeat(64), warning_count: 1, failed_count: 0, blocked_count: 0 },
  human_review: { ...review, current_render: { id: renderId, status: "completed", phase: "completed", progress: 1, frames_rendered: 900, total_frames: 900, render_fingerprint: fingerprint, output_sha256: sha, updated_at: now } },
  music_override_asset_id: null, music_override_revision: 0,
};
const timeline = {
  id: "55555555-5555-4555-8555-555555555555", fingerprint: "e".repeat(64), schema_version: "production_timeline_v1",
  strategy_version: "hybrid_v1", validator_version: "v1", status: "completed", duration_seconds: 30,
  visual_coverage_ratio: 0.9, gap_count: 1, required_gap_seconds: 0, preferred_gap_seconds: 3,
  optional_gap_seconds: 0, background_gap_seconds: 0, unplaced_query_count: 0,
  tracks: { primary_visual: { placements: [{ id: "p1", order: 0, visual_query_ids: ["q1"], timeline_start_seconds: 0, timeline_end_seconds: 27, source: { source_type: "video_segment", source_start_seconds: 10, source_end_seconds: 37, provenance: { media_filename: "session_01.mp4" } }, playback_rate: 1, presentation: null, transition: "cut", strategy: "semantic", importance: "required", diagnostics: null }] } },
  gaps: [{ id: "g1", order: 0, timeline_start_seconds: 27, timeline_end_seconds: 30, reason: "Cobertura preferida indisponível", severity: "preferred", related_visual_query_ids: ["q1"], diagnostics: null }],
  query_placement_outcomes: [{ visual_query_id: "q1", status: "placed" }], metrics: {}, diagnostics: null, error_message: null,
};
const music = { asset_id: "gta-analysis-01", sha256: "f".repeat(64), duration_seconds: 120, sample_rate: 48000, channels: 2, format: "wav", integrated_lufs: -21.5, true_peak_dbtp: -2.1, moods: ["analytical"], styles: ["electronic"], energy: 0.45, loopable: true, source_owner: "NeoVids", license_type: "owned", monetization_allowed: true, attribution_required: false, attribution_text: null };
const routes = {
  "/dashboard/stats": { fila_nome: "celery", fila_status: "available", fila_tamanho: 0, fila_error: null, workflows_ativos: 0, blocos_concluidos_24h: 1, blocos_erro_24h: 0, duracao_media_audio_bloco_segundos: 8, taxa_erro_24h_percent: 0, generation_latency_p50_seconds: 2, generation_latency_p95_seconds: 3, failed_takes_24h: 0, quality_retries_24h: 0, average_attempts_per_completed_block_24h: 1, workflow_completion_time_mean_seconds_24h: 60, render_factor_mean_24h: 0.2, qa_success_rate_24h_percent: 100 },
  "/health/dependencias": { status: "degraded", dependencies: { database: { status: "healthy", latency_ms: 1.2, error: null }, qwen_voice: { status: "unavailable", latency_ms: null, error: "desligado no smoke" } } },
  "/operator/workflows": [summary],
  [`/operator/workflows/${workflowId}`]: { schema_version: "operator_console_v1", workflow: summary, artifacts: { narration_alignment: { id: "44444444-4444-4444-8444-444444444444", fingerprint: "d".repeat(64), status: "completed" }, visual_plan: { id: "66666666-6666-4666-8666-666666666666", fingerprint: "d".repeat(64), status: "completed" }, production_timeline: { id: timeline.id, fingerprint: timeline.fingerprint, status: "completed" }, presentation_fingerprint: "1".repeat(64), audio_mix_plan_fingerprint: "2".repeat(64) } },
  [`/workflow/${workflowId}`]: { id: workflowId, roteiro_id: "77777777-7777-4777-8777-777777777777", status: "audio_concluido", audio_available: true, audio_final_sha256: "3".repeat(64), duracao_audio_segundos: 29.8, audio_final_sample_rate: 48000, audio_final_channels: 1, audio_final_subtype: "PCM_16", mastering_config: { profile_id: "narration_v3" }, mastering_metrics: { integrated_lufs: -16 }, production_status: "completed", production_phase: "completed", production_error_category: null, production_error_message: null, production_error_history: [], production_started_at: now, production_completed_at: now, audio_edit_revision: 0, audio_materialized_revision: 0, audio_edits_pending: false, criado_em: now, atualizado_em: now, blocos: [], total_blocos: 0, blocos_concluidos: 0, blocos_pendentes: 0, blocos_gerando: 0, blocos_erro: 0 },
  [`/workflow/${workflowId}/visual-plan`]: { id: "66666666-6666-4666-8666-666666666666", fingerprint: "d".repeat(64), strategy_version: "visual_director_v1", status: "completed", total_queries: 1, resolved_queries: 1, unresolved_queries: 0, failed_queries: 0, required_gaps: 0, optional_gaps: 0, metrics: {}, diagnostics: null, error_message: null, decisions: [{ id: "v1", visual_query_id: "q1", block_order: 0, query_order: 0, query: "GTA city chase", intent: "context", importance: "required", desired_duration_seconds: 10, narration_binding_status: "resolved", narration_start_seconds: 0, narration_end_seconds: 10, source_resolution_status: "resolved", selection_strategy: "semantic", selected_source: { source_type: "video_segment", media_filename: "session_01.mp4", selection_score: 0.91, semantic_score: 0.88, rank: 1, source_start_seconds: 10, source_end_seconds: 20, source_duration_seconds: 10, session_label: "GTA Session 01", transcript_preview: "perseguição no centro", provenance: { corpus: "gta" } }, alternatives: [], selection_score: 0.91, score_components: { semantic: 0.88 }, reason: "Maior score válido", diagnostics: null }] },
  [`/workflow/${workflowId}/production-timeline`]: timeline,
  [`/operator/workflows/${workflowId}/composer-inspection`]: { schema_version: "operator_composer_inspection_v1", production_timeline: timeline, presentation: { schema_version: "presentation_spec_v1", strategy_version: "v1", profile_id: "gta", fingerprint: "1".repeat(64), captions: { policy_version: "v1", preset: "default", cues: [{ text: "A perseguição começa", start_seconds: 0, end_seconds: 2 }] }, section_titles: [{ text: "Abertura", start_seconds: 0, end_seconds: 3, preset: "default" }], branding: { enabled: true, preset: "neovids" }, transitions: [{ type: "fade_to_black", boundary_seconds: 27, duration_seconds: 0.4, preset: "default" }] }, audio_mix_plan: { schema_version: "audio_mix_plan_v1", strategy_version: "v1", profile_id: "gta", duration_seconds: 30, fingerprint: "2".repeat(64), narration: { sha256: "3".repeat(64), duration_seconds: 29.8, sample_rate: 48000, channels: 1 }, music: { enabled: true, asset: { ...music, byte_size: 10, content_type: "audio/wav", vocals: false }, source_start_seconds: 0, source_end_seconds: 30, loop_policy: "loop", normalization_policy: "lufs", normalization_target_lufs: -23, segments: [{ start_seconds: 0, end_seconds: 30, gain_adjustment_db: -0.2, fade_in_seconds: 0.4, fade_out_seconds: 0.4, transition_preset: "smooth_v1" }] }, ducking: { policy_version: "v1", base_gain_db: -8, ducked_gain_db: -18, attack_seconds: 0.1, release_seconds: 0.5, speech_regions: [{ start_seconds: 0, end_seconds: 29.8 }] }, program_master: { profile: "program_v1", target_lufs: -14, true_peak_ceiling_dbtp: -1, target_lra: 7 }, output: { codec: "aac", bitrate: "192k", sample_rate: 48000, channels: 2 } } },
  "/operator/music-assets": { schema_version: "operator_music_catalog_v1", assets: [music] },
  "/operator/review-queue": [{ workflow: summary, category: "qa_warning_review", priority: 2 }],
  [`/workflow/${workflowId}/render`]: { id: renderId, workflow_id: workflowId, production_timeline_id: timeline.id, production_timeline_fingerprint: timeline.fingerprint, composer_render_id: "smoke", render_profile: "gta", mode: "production", status: "completed", phase: "completed", progress: 1, frames_rendered: 900, total_frames: 900, render_profile_identity: "gta@1", render_fingerprint: fingerprint, manifest_fingerprint: "4".repeat(64), output: { sha256: sha, byte_size: 1048576, duration_seconds: 30, width: 1920, height: 1080, fps: 30, frame_count: 900, video_codec: "h264", audio_codec: "aac", audio_sample_rate: 48000 }, metrics: { render_factor: 0.2 }, error: null, created_at: now, started_at: now, completed_at: now, updated_at: now, video_url: null },
  [`/workflow/${workflowId}/qa-report`]: { id: summary.qa.id, workflow_id: workflowId, workflow_render_id: renderId, production_timeline_id: timeline.id, visual_plan_id: "66666666-6666-4666-8666-666666666666", narration_alignment_id: "44444444-4444-4444-8444-444444444444", final_audio_sha256: "3".repeat(64), render_output_sha256: sha, qa_mode: "standard", policy_name: "stage18", policy_version: "v1", policy_identity: "stage18@1", strategy_version: "v1", request_fingerprint: summary.qa.request_fingerprint, status: "passed_with_warnings", publication_ready: true, check_count: 1, passed_count: 0, warning_count: 1, failed_count: 0, blocked_count: 0, checks: [{ code: "visual_gap", category: "visual", result: "warning", message: "Gap preferido presente", expected: 0, actual: 3, details: { severity: "preferred" } }], metrics: {}, error_category: null, error_message: null, created_at: now, started_at: now, completed_at: now, updated_at: now },
  [`/operator/workflows/${workflowId}/history`]: { workflow_id: workflowId, created_at: now, production_error_history: [], audio_edit_revision: 0, audio_materialized_revision: 0, renders: [{ id: renderId, status: "completed", phase: "completed", render_profile: "gta", request_fingerprint: "5".repeat(64), render_fingerprint: fingerprint, manifest_fingerprint: "4".repeat(64), output_sha256: sha, output_duration_seconds: 30, error_category: null, error_message: null, created_at: now, completed_at: now }], qa_reports: [{ id: summary.qa.id, workflow_render_id: renderId, qa_mode: "standard", status: "passed_with_warnings", publication_ready: true, request_fingerprint: summary.qa.request_fingerprint, warning_count: 1, failed_count: 0, blocked_count: 0, error_category: null, error_message: null, created_at: now, completed_at: now }], human_reviews: [] },
  "/workflow/roteiros-disponiveis": [{ id: "77777777-7777-4777-8777-777777777777", ideia_id: "88888888-8888-4888-8888-888888888888", versao: 3, titulo: "GTA — Operação Smoke", duracao_total_segundos: 30, num_blocos: 1 }],
};

createServer((request, response) => {
  const url = new URL(request.url || "/", "http://127.0.0.1");
  const data = routes[url.pathname];
  response.setHeader("Access-Control-Allow-Origin", "*");
  response.setHeader("Content-Type", "application/json; charset=utf-8");
  if (data === undefined) { response.statusCode = 404; response.end(JSON.stringify({ detail: "smoke fixture: not found" })); return; }
  response.end(JSON.stringify(data));
}).listen(8015, "127.0.0.1", () => console.log(`Operator smoke API: http://127.0.0.1:8015 (${workflowId})`));
