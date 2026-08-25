const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8011";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `Erro ${resp.status}`);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json();
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
  status: string;
  synthesis_text: string | null;
  voice_profile: string | null;
  generation_profile: string | null;
  resolved_seed: number | null;
  generation_attempt: number;
  duracao_segundos: number | null;
  audio_sha256: string | null;
  audio_sample_rate: number | null;
  audio_channels: number | null;
  audio_byte_size: number | null;
  narration_start_seconds: number | null;
  narration_end_seconds: number | null;
  erro_mensagem: string | null;
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
  erro_mensagem: string | null;
  tentativas: number;
  production_status: string | null;
  production_phase: string | null;
  production_error_category: string | null;
  production_error_message: string | null;
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
  fila_tamanho: number;
  workflows_ativos: number;
  blocos_concluidos_24h: number;
  blocos_erro_24h: number;
  tempo_medio_bloco_segundos: number | null;
  taxa_erro_24h_percent: number;
}

export const api = {
  listarRoteirosDisponiveis: () => request<RoteiroDisponivel[]>("/workflow/roteiros-disponiveis"),
  listarWorkflows: (status?: string) => {
    const params = status ? `?status=${status}` : "";
    return request<Workflow[]>(`/workflow${params}`);
  },
  obterWorkflow: (id: string) => request<Workflow>(`/workflow/${id}`),
  criarWorkflow: (roteiro_id: string) =>
    request<Workflow>("/workflow", { method: "POST", body: JSON.stringify({ roteiro_id }) }),
  repetirWorkflow: (id: string) => request<Workflow>(`/workflow/${id}/retry`, { method: "PATCH" }),
  cancelarWorkflow: (id: string) => request<Workflow>(`/workflow/${id}/cancelar`, { method: "PATCH" }),
  retomarProducao: (id: string) => request<Workflow>(`/workflow/${id}/resume-production`, { method: "POST" }),
  obterRender: (id: string) => request<WorkflowRender>(`/workflow/${id}/render`),
  obterQAReport: (id: string, mode: "standard" | "deep" = "standard") =>
    request<ProductionQAReport>(`/workflow/${id}/qa-report?mode=${mode}`),
  executarQA: (
    id: string,
    mode: "standard" | "deep" = "standard",
    force = false,
  ) =>
    request<ProductionQAEnsure>(
      `/workflow/${id}/qa-report?mode=${mode}&force=${force}`,
      { method: "POST" },
    ),
  obterDashboardStats: () => request<DashboardStats>("/dashboard/stats"),
  urlAudio: (id: string) => `${BASE_URL}/workflow/${id}/audio`,
  urlAudioBloco: (workflowId: string, blocoId: string) => `${BASE_URL}/workflow/${workflowId}/blocos/${blocoId}/audio`,
  urlVideo: (id: string) => `${BASE_URL}/workflow/${id}/video`,
};
