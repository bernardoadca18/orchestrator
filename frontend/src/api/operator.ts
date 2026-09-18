import { BASE_URL, request } from "./client";

export interface ActionCapability {
  allowed: boolean;
  reason: string | null;
  invalidates: string[];
}

export interface AttentionReason {
  code: string;
  severity: "info" | "warning" | "critical";
  message: string;
}

export interface RenderIdentity {
  id: string;
  status: string;
  phase: string;
  progress: number;
  frames_rendered: number;
  total_frames: number | null;
  render_fingerprint: string | null;
  output_sha256: string | null;
  updated_at: string;
}

export interface QAIdentity {
  id: string;
  status: string;
  publication_ready: boolean;
  qa_mode: string;
  request_fingerprint: string;
  warning_count: number;
  failed_count: number;
  blocked_count: number;
}

export type ReviewDecision = "approved" | "changes_requested" | "rejected";
export type ReviewState = "unavailable" | "pending" | ReviewDecision | "stale";

export interface HumanReviewRecord {
  id: string;
  workflow_execucao_id: string;
  workflow_render_id: string;
  render_fingerprint: string;
  output_sha256: string;
  qa_report_id: string | null;
  qa_request_fingerprint: string | null;
  status: ReviewDecision;
  note: string | null;
  operator_label: string | null;
  created_at: string;
}

export interface HumanReviewState {
  state: ReviewState;
  current: HumanReviewRecord | null;
  latest_historical: HumanReviewRecord | null;
  current_render: RenderIdentity | null;
}

export interface OperatorWorkflowSummary {
  id: string;
  title: string;
  script_duration_seconds: number | null;
  audio_duration_seconds: number | null;
  production_profile: string | null;
  legacy_status: string;
  production_status: string | null;
  production_phase: string | null;
  created_at: string;
  updated_at: string;
  current_error_category: string | null;
  current_error_message: string | null;
  attention: AttentionReason[];
  capabilities: Record<string, ActionCapability>;
  render: RenderIdentity | null;
  qa: QAIdentity | null;
  human_review: HumanReviewState;
  music_override_asset_id: string | null;
  music_override_revision: number;
}

export interface ArtifactIdentity {
  id: string;
  fingerprint: string;
  status: string;
}

export interface OperatorWorkflowSnapshot {
  schema_version: "operator_console_v1";
  workflow: OperatorWorkflowSummary;
  artifacts: {
    narration_alignment: ArtifactIdentity | null;
    visual_plan: ArtifactIdentity | null;
    production_timeline: ArtifactIdentity | null;
    presentation_fingerprint: string | null;
    audio_mix_plan_fingerprint: string | null;
  };
}

export interface VisualSource {
  source_type: "gameplay" | "video_segment" | "static_image";
  media_filename: string;
  selection_score: number;
  semantic_score: number | null;
  rank: number | null;
  source_start_seconds?: number;
  source_end_seconds?: number;
  source_duration_seconds?: number;
  candidate_start_seconds?: number;
  candidate_end_seconds?: number;
  session_label?: string;
  transcript_preview?: string | null;
  width?: number;
  height?: number;
  semantic_preview?: string | null;
  provenance: Record<string, unknown>;
  semantic_index?: Record<string, unknown>;
}

export interface VisualDecision {
  id: string;
  visual_query_id: string;
  block_order: number;
  query_order: number;
  query: string;
  intent: string;
  importance: string;
  desired_duration_seconds: number | null;
  narration_binding_status: string;
  narration_start_seconds: number | null;
  narration_end_seconds: number | null;
  source_resolution_status: string;
  selection_strategy: string | null;
  selected_source: VisualSource | null;
  alternatives: VisualSource[];
  selection_score: number | null;
  score_components: Record<string, unknown> | null;
  reason: string | null;
  diagnostics: Record<string, unknown> | null;
}

export interface VisualPlanDetail {
  id: string;
  fingerprint: string;
  strategy_version: string;
  status: string;
  total_queries: number;
  resolved_queries: number;
  unresolved_queries: number;
  failed_queries: number;
  required_gaps: number;
  optional_gaps: number;
  metrics: Record<string, unknown> | null;
  diagnostics: Record<string, unknown> | null;
  error_message: string | null;
  decisions: VisualDecision[];
}

export interface TimelinePlacement {
  id: string;
  order: number;
  visual_query_ids: string[];
  timeline_start_seconds: number;
  timeline_end_seconds: number;
  source: Record<string, unknown> & { source_type: string };
  playback_rate: number | null;
  presentation: Record<string, unknown> | null;
  transition: string;
  strategy: string;
  importance: string;
  diagnostics: Record<string, unknown> | null;
}

export interface TimelineGap {
  id: string;
  order: number;
  timeline_start_seconds: number;
  timeline_end_seconds: number;
  reason: string;
  severity: string;
  related_visual_query_ids: string[];
  diagnostics: Record<string, unknown> | null;
}

export interface ProductionTimelineDetail {
  id: string;
  fingerprint: string;
  schema_version: string;
  strategy_version: string;
  validator_version: string;
  status: string;
  duration_seconds: number;
  visual_coverage_ratio: number;
  visual_gap_seconds: number;
  gap_count: number;
  required_gap_seconds: number;
  preferred_gap_seconds: number;
  optional_gap_seconds: number;
  background_gap_seconds: number;
  unplaced_query_count: number;
  tracks: { primary_visual: { placements: TimelinePlacement[] } };
  gaps: TimelineGap[];
  query_placement_outcomes: Array<Record<string, unknown>>;
  metrics: Record<string, unknown> | null;
  diagnostics: Record<string, unknown> | null;
  error_message: string | null;
}

export interface PresentationSpec {
  schema_version: string;
  strategy_version: string;
  profile_id: string;
  fingerprint: string;
  captions: { policy_version: string; preset: string; cues: Array<{ text: string; start_seconds: number; end_seconds: number }> };
  section_titles: Array<{ text: string; start_seconds: number; end_seconds: number; preset: string }>;
  branding: Record<string, unknown> & { enabled: boolean; preset: string };
  transitions: Array<{ type: string; boundary_seconds: number; duration_seconds: number; preset: string }>;
}

export interface MusicAsset {
  asset_id: string;
  sha256: string;
  duration_seconds: number;
  sample_rate: number;
  channels: number;
  format: string;
  integrated_lufs: number;
  true_peak_dbtp: number;
  moods: string[];
  styles: string[];
  energy: number;
  loopable: boolean;
  source_owner: string;
  license_type: string;
  monetization_allowed: boolean;
  attribution_required: boolean;
  attribution_text: string | null;
}

export interface AudioMixPlan {
  schema_version: string;
  strategy_version: string;
  profile_id: string;
  duration_seconds: number;
  fingerprint: string;
  narration: { sha256: string; duration_seconds: number; sample_rate: number; channels: number };
  music: { enabled: false } | {
    enabled: true;
    asset: MusicAsset & { byte_size: number; content_type: string; vocals: boolean };
    source_start_seconds: number;
    source_end_seconds: number;
    loop_policy: string;
    normalization_policy: string;
    normalization_target_lufs: number;
    segments: Array<Record<string, number | string>>;
  };
  ducking: {
    policy_version: string;
    base_gain_db: number;
    ducked_gain_db: number;
    attack_seconds: number;
    release_seconds: number;
    speech_regions: Array<{ start_seconds: number; end_seconds: number }>;
  };
  program_master: Record<string, string | number>;
  output: Record<string, string | number>;
}

export interface ComposerInput {
  schema_version: "composer_render_input_v1" | "composer_render_input_v2" | "operator_composer_inspection_v1";
  production_timeline: ProductionTimelineDetail;
  presentation: PresentationSpec | null;
  audio_mix_plan?: AudioMixPlan;
}

export interface ReviewQueueItem {
  workflow: OperatorWorkflowSummary;
  category: "final_video_review" | "qa_warning_review" | "changes_requested" | "audio_attention";
  priority: number;
}

export interface MusicOverrideResult {
  workflow_id: string;
  asset_id: string | null;
  revision: number;
  changed: boolean;
  production_status: string | null;
  production_phase: string | null;
}

export interface OperatorWorkflowHistory {
  workflow_id: string;
  created_at: string;
  production_error_history: Array<{
    category: string; message: string; status: string; phase: string; occurred_at: string;
    recovered_at?: string | null; recovery_status?: string; recovery_phase?: string;
  }>;
  audio_edit_revision: number;
  audio_materialized_revision: number;
  renders: Array<{
    id: string; status: string; phase: string; render_profile: string;
    request_fingerprint: string; render_fingerprint: string | null;
    manifest_fingerprint: string | null; output_sha256: string | null;
    output_duration_seconds: number | null; error_category: string | null;
    error_message: string | null; created_at: string; completed_at: string | null;
  }>;
  qa_reports: Array<{
    id: string; workflow_render_id: string; qa_mode: string; status: string;
    publication_ready: boolean; request_fingerprint: string; warning_count: number;
    failed_count: number; blocked_count: number; error_category: string | null;
    error_message: string | null; created_at: string; completed_at: string | null;
  }>;
  human_reviews: HumanReviewRecord[];
}

export interface WorkflowListFilters {
  [key: string]: string | number | undefined;
  state?: string;
  search?: string;
  production_status?: string;
  review_status?: string;
  ordering?: string;
  limit?: number;
  offset?: number;
}

function params(values: Record<string, string | number | undefined>) {
  const result = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== "") result.set(key, String(value));
  });
  const encoded = result.toString();
  return encoded ? `?${encoded}` : "";
}

export type VoiceLanguage = "Portuguese" | "English";

export interface VoiceProfiles {
  profiles: Array<{ name: string; clone_mode: string; reference_id: string | null; version: string; identity: string }>;
  defaults: Record<"pt-BR" | "en-US", string>;
}

export interface VoicePreviewInput {
  voice_profile: string;
  language: VoiceLanguage;
  text: string;
}

export const operatorApi = {
  getVoiceProfiles: (signal?: AbortSignal) => request<VoiceProfiles>("/operator/voice-profiles", { signal }),
  previewVoice: (data: VoicePreviewInput, signal?: AbortSignal) =>
    request<Blob>("/operator/voice-preview", { method: "POST", body: JSON.stringify(data), signal }, "blob"),
  listWorkflows: (filters: WorkflowListFilters = {}, signal?: AbortSignal) =>
    request<OperatorWorkflowSummary[]>(`/operator/workflows${params(filters)}`, { signal }),
  getWorkflow: (id: string, signal?: AbortSignal) =>
    request<OperatorWorkflowSnapshot>(`/operator/workflows/${id}`, { signal }),
  getWorkflowHistory: (id: string, signal?: AbortSignal) =>
    request<OperatorWorkflowHistory>(`/operator/workflows/${id}/history`, { signal }),
  getReviewQueue: (signal?: AbortSignal) =>
    request<ReviewQueueItem[]>("/operator/review-queue", { signal }),
  getMusicCatalog: (signal?: AbortSignal) =>
    request<{ schema_version: string; assets: MusicAsset[] }>("/operator/music-assets", { signal }),
  getVisualPlan: (id: string, signal?: AbortSignal) =>
    request<VisualPlanDetail>(`/workflow/${id}/visual-plan`, { signal }),
  repairTimeline: (id: string) =>
    request(`/workflow/${id}/repair-production-timeline`, { method: "POST" }),
  getTimeline: (id: string, signal?: AbortSignal) =>
    request<ProductionTimelineDetail>(`/workflow/${id}/production-timeline`, { signal }),
  getComposerInput: (id: string, signal?: AbortSignal) =>
    request<ComposerInput>(`/operator/workflows/${id}/composer-inspection`, { signal }),
  getReviewHistory: (id: string, signal?: AbortSignal) =>
    request<HumanReviewRecord[]>(`/workflow/${id}/human-review/history`, { signal }),
  submitReview: (
    id: string,
    input: {
      workflow_render_id: string;
      render_fingerprint: string;
      output_sha256: string;
      qa_report_id?: string | null;
      qa_request_fingerprint?: string | null;
      status: ReviewDecision;
      note?: string | null;
    },
    key: string,
  ) => request<{ created: boolean; review: HumanReviewRecord }>(`/workflow/${id}/human-review`, {
    method: "POST",
    headers: { "Idempotency-Key": key },
    body: JSON.stringify(input),
  }),
  setMusicOverride: (id: string, assetId: string, expectedRevision: number, key: string) =>
    request<MusicOverrideResult>(`/workflow/${id}/music-override`, {
      method: "PUT",
      headers: { "Idempotency-Key": key },
      body: JSON.stringify({ asset_id: assetId, expected_revision: expectedRevision }),
    }),
  clearMusicOverride: (id: string, expectedRevision: number, key: string) =>
    request<MusicOverrideResult>(`/workflow/${id}/music-override`, {
      method: "DELETE",
      headers: { "Idempotency-Key": key },
      body: JSON.stringify({ expected_revision: expectedRevision }),
    }),
  musicPreviewUrl: (asset: Pick<MusicAsset, "asset_id" | "sha256">) =>
    `${BASE_URL}/operator/music-assets/${encodeURIComponent(asset.asset_id)}/audio?sha256=${encodeURIComponent(asset.sha256)}`,
};
