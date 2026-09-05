const BASE_URL = import.meta.env?.VITE_API_URL || "http://localhost:8011";

export type ApiErrorKind = "http" | "network" | "malformed_response";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly kind: ApiErrorKind,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function isExpectedNotFound(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 404;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const { headers: optionHeaders, ...rest } = options || {};
  let resp: Response;
  try {
    resp = await fetch(`${BASE_URL}${path}`, {
      ...rest,
      headers: { "Content-Type": "application/json", ...(optionHeaders || {}) },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("Não foi possível conectar ao Orchestrator.", "network");
  }
  if (!resp.ok) {
    const body = await resp.json().catch(() => null) as { detail?: string | { message?: string } } | null;
    const detail = typeof body?.detail === "string" ? body.detail : body?.detail?.message;
    throw new ApiError(detail || `Orchestrator retornou HTTP ${resp.status}.`, "http", resp.status);
  }
  if (resp.status === 204) return undefined as T;
  try {
    return await resp.json() as T;
  } catch {
    throw new ApiError("O Orchestrator retornou uma resposta inválida.", "malformed_response", resp.status);
  }
}

export interface RoteiroDisponivel {
  id: string;
  ideia_id: string;
  versao: number;
  titulo: string;
  duracao_total_segundos: number;
  num_blocos: number;
}

export interface AudioBloco {
  id: string;
  ordem: number;
  tipo: string;
  texto: string;
  normalized_text: string | null;
  status: string;
  synthesis_text: string | null;
  text_normalizer_version: string | null;
  text_normalizer_identity: string | null;
  pronunciation_glossary_identity: string | null;
  pronunciation_replacements: PronunciationReplacement[] | null;
  voice_profile: string | null;
  generation_profile: string | null;
  resolved_seed: number | null;
  initial_resolved_seed: number | null;
  voice_chunking_identity: string | null;
  current_take_id: string | null;
  requested_take_id: string | null;
  generation_attempt: number;
  generation_attempt_history: Array<Record<string, unknown>> | null;
  takes: AudioBlocoTake[];
  duracao_segundos: number | null;
  audio_sha256: string | null;
  audio_sample_rate: number | null;
  audio_channels: number | null;
  audio_byte_size: number | null;
  audio_frame_count: number | null;
  audio_subtype: string | null;
  artifact_integrity: Record<string, unknown> | null;
  pause_diagnostics: Record<string, unknown> | null;
  narration_start_seconds: number | null;
  narration_end_seconds: number | null;
  erro_mensagem: string | null;
}

export interface PronunciationReplacement {
  entry_id?: string;
  entry_version?: number;
  original_text?: string;
  replacement_text?: string;
  changed?: boolean;
  input_start?: number;
  input_end?: number;
  term?: string;
  replacement?: string;
  count?: number;
}

export interface AudioChunk {
  id: string;
  bloco_id: string;
  chunk_index: number;
  chunk_id: string;
  text: string | null;
  synthesis_text: string | null;
  seed: number;
  duration_seconds: number | null;
  sample_rate: number | null;
  current_take_id: string | null;
  takes: AudioChunkTake[];
  created_at: string;
  updated_at: string;
}

export interface AudioChunkTake {
  id: string;
  chunk_id: string;
  kind: "initial" | "regeneration";
  status: "candidate" | "active" | "superseded" | "rejected" | "failed";
  seed: number;
  audio_sha256: string | null;
  audio_byte_size: number | null;
  audio_duration_seconds: number | null;
  audio_sample_rate: number | null;
  audio_frame_count: number | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface AudioBlocoTake {
  id: string;
  attempt_index: number;
  kind: string;
  status: string;
  request_key: string | null;
  job_id_voz: string | null;
  idempotency_key: string | null;
  requested_seed: number | null;
  resolved_seed: number | null;
  model_id: string | null;
  model_revision: string | null;
  voice_profile: string | null;
  voice_profile_identity: string | null;
  generation_profile: string | null;
  generation_profile_identity: string | null;
  voice_chunking_identity: string | null;
  chunking_identity: string | null;
  synthesis_identity: string | null;
  canonical_text: string | null;
  normalized_text: string | null;
  synthesis_text: string | null;
  pronunciation_glossary_identity: string | null;
  pronunciation_replacements: PronunciationReplacement[] | null;
  text_normalizer_version: string | null;
  text_normalizer_identity: string | null;
  error_category: string | null;
  error_message: string | null;
  error_diagnostics: Record<string, unknown> | null;
  audio_duration_seconds: number | null;
  audio_sha256: string | null;
  audio_byte_size: number | null;
  audio_sample_rate: number | null;
  audio_channels: number | null;
  audio_frame_count: number | null;
  audio_subtype: string | null;
  quality_status: string | null;
  artifact_integrity: Record<string, unknown> | null;
  provider_metadata: Record<string, unknown> | null;
  content_quality: Record<string, unknown> | null;
  pause_diagnostics: Record<string, unknown> | null;
  is_current: boolean;
  created_at: string;
  completed_at: string | null;
}

export interface Workflow {
  id: string;
  ideia_id: string;
  roteiro_id: string;
  perfil_producao?: string | null;
  status: string;
  audio_available: boolean;
  duracao_audio_segundos: number | null;
  audio_phase: string;
  audio_final_sha256: string | null;
  audio_final_sample_rate: number | null;
  audio_final_channels: number | null;
  audio_final_byte_size: number | null;
  audio_final_frame_count: number | null;
  audio_final_subtype: string | null;
  audio_integrity: Record<string, unknown> | null;
  erro_mensagem: string | null;
  tentativas: number;
  production_status: string | null;
  production_phase: string | null;
  production_error_category: string | null;
  production_error_message: string | null;
  production_error_history: Array<{
    category: string;
    message: string;
    status: string;
    phase: string;
    occurred_at: string;
    recovered_at?: string | null;
    recovery_status?: string;
    recovery_phase?: string;
  }> | null;
  production_started_at: string | null;
  production_completed_at: string | null;
  criado_em: string;
  atualizado_em: string;
  blocos?: AudioBloco[];
  total_blocos?: number;
  blocos_concluidos?: number;
  blocos_pendentes?: number;
  blocos_gerando?: number;
  blocos_erro?: number;
}

export interface WorkflowRender {
  id: string;
  workflow_id: string;
  production_timeline_id: string;
  production_timeline_fingerprint: string;
  composer_render_id: string | null;
  render_profile: string;
  mode: string;
  status: string;
  phase: string;
  progress: number;
  frames_rendered: number;
  total_frames: number | null;
  render_profile_identity: string | null;
  render_fingerprint: string | null;
  manifest_fingerprint: string | null;
  output: {
    sha256: string;
    byte_size: number;
    duration_seconds: number;
    width: number;
    height: number;
    fps: number;
    frame_count: number | null;
    video_codec: string;
    audio_codec: string;
    audio_sample_rate: number;
  } | null;
  metrics: Record<string, unknown> | null;
  error: { category: string; message: string } | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
  video_url: string | null;
}

export interface NarrationTimeline {
  narration_alignment_id: string;
  fingerprint: string;
  status: string;
  resolved_visual_queries: number;
  unresolved_visual_queries: number;
  ambiguous_visual_queries: number;
  blocks: Array<{ overall_coverage: number }>;
  error_message: string | null;
}

export interface VisualPlan {
  id: string;
  fingerprint: string;
  status: string;
  total_queries: number;
  resolved_queries: number;
  unresolved_queries: number;
  failed_queries: number;
  required_gaps: number;
  error_message: string | null;
}

export interface ProductionTimeline {
  id: string;
  fingerprint: string;
  status: string;
  visual_coverage_ratio: number;
  gap_count: number;
  unplaced_query_count: number;
  required_gap_seconds: number;
  error_message: string | null;
}

export interface ProductionQACheck {
  code: string;
  category: string;
  result: "passed" | "warning" | "failed" | "blocked" | "skipped";
  message: string;
  expected: unknown | null;
  actual: unknown | null;
  details: Record<string, unknown> | null;
}

export interface ProductionQAReport {
  id: string;
  workflow_id: string;
  workflow_render_id: string;
  production_timeline_id: string;
  visual_plan_id: string | null;
  narration_alignment_id: string | null;
  final_audio_sha256: string;
  render_output_sha256: string;
  qa_mode: "standard" | "deep";
  policy_name: string;
  policy_version: string;
  policy_identity: string;
  strategy_version: string;
  request_fingerprint: string;
  status: string;
  publication_ready: boolean;
  check_count: number;
  passed_count: number;
  warning_count: number;
  failed_count: number;
  blocked_count: number;
  checks: ProductionQACheck[];
  metrics: Record<string, unknown> | null;
  error_category: string | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
}

export interface ProductionQAEnsure {
  created: boolean;
  report: ProductionQAReport;
}

export interface DashboardStats {
  fila_nome: string;
  fila_status: "available" | "unavailable";
  fila_tamanho: number | null;
  fila_error: string | null;
  workflows_ativos: number;
  blocos_concluidos_24h: number;
  blocos_erro_24h: number;
  duracao_media_audio_bloco_segundos: number | null;
  taxa_erro_24h_percent: number;
  generation_latency_p50_seconds: number | null;
  generation_latency_p95_seconds: number | null;
  failed_takes_24h: number;
  quality_retries_24h: number;
  average_attempts_per_completed_block_24h: number | null;
  workflow_completion_time_mean_seconds_24h: number | null;
  render_factor_mean_24h: number | null;
  qa_success_rate_24h_percent: number | null;
}

export interface DependencyHealth {
  status: "ready" | "degraded";
  dependencies: Record<string, {
    status: "healthy" | "unavailable";
    latency_ms: number | null;
    error: string | null;
  }>;
}

export const api = {
  listarRoteirosDisponiveis: (signal?: AbortSignal) => request<RoteiroDisponivel[]>("/workflow/roteiros-disponiveis", { signal }),
  listarWorkflows: (status?: string, signal?: AbortSignal) => {
    const params = status ? `?status=${status}` : "";
    return request<Workflow[]>(`/workflow${params}`, { signal });
  },
  obterWorkflow: (id: string, signal?: AbortSignal) => request<Workflow>(`/workflow/${id}`, { signal }),
  criarWorkflow: (roteiro_id: string) =>
    request<Workflow>("/workflow", { method: "POST", body: JSON.stringify({ roteiro_id }) }),
  repetirWorkflow: (id: string) => request<Workflow>(`/workflow/${id}/retry`, { method: "PATCH" }),
  retryAudioBlock: (workflowId: string, order: number, key: string) =>
    request<Workflow>(`/workflow/${workflowId}/audio/blocks/${order}/retry`, {
      method: "POST", headers: { "Idempotency-Key": key },
    }),
  regenerateAudioBlock: (workflowId: string, order: number, key: string) =>
    request<Workflow>(`/workflow/${workflowId}/audio/blocks/${order}/regenerate`, {
      method: "POST", headers: { "Idempotency-Key": key },
    }),
  regenerateNarration: (workflowId: string, key: string) =>
    request<Workflow>(`/workflow/${workflowId}/audio/regenerate`, {
      method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": key }, body: JSON.stringify({ confirm: true }),
    }),
  cancelarWorkflow: (id: string) => request<Workflow>(`/workflow/${id}/cancelar`, { method: "PATCH" }),
  retomarProducao: (id: string) => request<Workflow>(`/workflow/${id}/resume-production`, { method: "POST" }),
  obterNarrationTimeline: (id: string, signal?: AbortSignal) =>
    request<NarrationTimeline>(`/workflow/${id}/narration-timeline`, { signal }),
  obterVisualPlan: (id: string, signal?: AbortSignal) =>
    request<VisualPlan>(`/workflow/${id}/visual-plan`, { signal }),
  obterProductionTimeline: (id: string, signal?: AbortSignal) =>
    request<ProductionTimeline>(`/workflow/${id}/production-timeline`, { signal }),
  obterRender: (id: string, signal?: AbortSignal) => request<WorkflowRender>(`/workflow/${id}/render`, { signal }),
  obterQAReport: (id: string, mode: "standard" | "deep" = "standard", signal?: AbortSignal) =>
    request<ProductionQAReport>(`/workflow/${id}/qa-report?mode=${mode}`, { signal }),
  executarQA: (
    id: string,
    mode: "standard" | "deep" = "standard",
    force = false,
  ) =>
    request<ProductionQAEnsure>(
      `/workflow/${id}/qa-report?mode=${mode}&force=${force}`,
      { method: "POST" },
    ),
  obterDashboardStats: (signal?: AbortSignal) => request<DashboardStats>("/dashboard/stats", { signal }),
  obterDependencias: (signal?: AbortSignal) => request<DependencyHealth>("/health/dependencias", { signal }),
  urlAudio: (id: string, sha256?: string | null) =>
    `${BASE_URL}/workflow/${id}/audio${sha256 ? `?artifact=${encodeURIComponent(sha256)}` : ""}`,
  urlAudioBloco: (workflowId: string, blocoId: string, sha256?: string | null) =>
    `${BASE_URL}/workflow/${workflowId}/blocos/${blocoId}/audio${sha256 ? `?artifact=${encodeURIComponent(sha256)}` : ""}`,
  urlVideo: (id: string) => `${BASE_URL}/workflow/${id}/video`,
  urlChunkTakeAudio: (workflowId: string, takeId: string, sha256?: string | null) =>
    `${BASE_URL}/workflow/${workflowId}/chunks/${takeId}/audio${sha256 ? `?artifact=${encodeURIComponent(sha256)}` : ""}`,
  listarChunks: (workflowId: string, blocoId: string, signal?: AbortSignal) =>
    request<AudioChunk[]>(`/workflow/${workflowId}/blocos/${blocoId}/chunks`, { signal }),
  regenerarChunk: (workflowId: string, blocoId: string, chunkIndex: number, seed: number, key: string) =>
    request<AudioChunkTake>(`/workflow/${workflowId}/blocos/${blocoId}/chunks/${chunkIndex}/regenerate`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": key },
      body: JSON.stringify({ seed }),
    }),
  promoverChunkTake: (workflowId: string, takeId: string, key: string) =>
    request<{ success: boolean; chunk: AudioChunk }>(`/workflow/${workflowId}/chunk-takes/${takeId}/promote`, {
      method: "POST",
      headers: { "Idempotency-Key": key },
    }),
  descartarChunkTake: (workflowId: string, takeId: string, key: string) =>
    request<{ success: boolean }>(`/workflow/${workflowId}/chunk-takes/${takeId}/discard`, {
      method: "POST",
      headers: { "Idempotency-Key": key },
    }),
  remontarBloco: (workflowId: string, blocoId: string, key: string) =>
    request<{ success: boolean; audio_sha256: string; duration_seconds: number }>(`/workflow/${workflowId}/blocos/${blocoId}/reassemble`, {
      method: "POST",
      headers: { "Idempotency-Key": key },
    }),
};
