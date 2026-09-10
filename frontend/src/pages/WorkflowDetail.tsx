import { useCallback, useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  api,
  isExpectedNotFound,
  NarrationTimeline,
  ProductionQAReport,
  ProductionTimeline,
  PronunciationReplacement,
  VisualPlan,
  Workflow,
  WorkflowRender,
} from "../api/client";
import { BlockChunks } from "../components/audio-edit/BlockChunks";
import { PendingAudioEdits } from "../components/audio-edit/PendingAudioEdits";
import { useAudioEditUpdate } from "../components/audio-edit/useAudioEditUpdate";
import { shouldPollQA, shouldPollRender } from "./polling";
import { presentContentQuality, qualityToneColor } from "../utils/contentQuality";
import { startSerialPolling } from "../utils/serialPolling";

const productionStages = [
  ["audio", "Audio"],
  ["narration_alignment", "Narration Alignment"],
  ["visual_plan", "Visual Plan"],
  ["production_timeline", "Production Timeline"],
  ["render", "Render"],
] as const;

async function optionalArtifact<T>(operation: () => Promise<T>): Promise<T | null> {
  try { return await operation(); }
  catch (error) {
    if (isExpectedNotFound(error)) return null;
    throw error;
  }
}

function formatPronunciation(item: PronunciationReplacement) {
  const original = item.original_text ?? item.term ?? "?";
  const replacement = item.replacement_text ?? item.replacement ?? "?";
  const identity = item.entry_id ? ` [${item.entry_id}@${item.entry_version ?? "?"}]` : "";
  const count = item.count && item.count > 1 ? ` (${item.count}x)` : "";
  return `${original} → ${replacement}${identity}${count}`;
}

function metadataText(metadata: Record<string, unknown> | null, key: string): string {
  const value = metadata?.[key];
  return typeof value === "string" || typeof value === "number" ? String(value) : "—";
}

function chunkSeeds(metadata: Record<string, unknown> | null): string {
  const chunks = metadata?.chunks;
  if (!Array.isArray(chunks)) return "—";
  const seeds = chunks.flatMap((item) => {
    if (!item || typeof item !== "object" || !("seed" in item)) return [];
    const seed = (item as { seed?: unknown }).seed;
    return typeof seed === "number" ? [String(seed)] : [];
  });
  return seeds.length > 0 ? seeds.join(", ") : "—";
}

function TakeQualityDetails({ report }: { report: Record<string, unknown> | null }) {
  const quality = presentContentQuality(report);
  if (!quality) return null;
  const color = qualityToneColor(quality.tone);
  return <>
    <span style={{ color }}> — {quality.outcomeLabel}: {quality.classificationLabel}</span>
    <details style={{ marginTop: "0.25rem" }}>
      <summary>Qualidade de conteúdo / ASR</summary>
      <div><strong>Decisão:</strong> {quality.outcomeLabel}</div>
      <div><strong>Classificação:</strong> {quality.classificationLabel}</div>
      <div><strong>Quality retry:</strong> {quality.retryLabel}</div>
      <dl style={{ display: "grid", gridTemplateColumns: "max-content max-content", gap: "0.15rem 0.75rem" }}>
        {quality.metrics.map(([label, value]) => <div key={label} style={{ display: "contents" }}>
          <dt>{label}</dt><dd style={{ margin: 0 }}>{value}</dd>
        </div>)}
      </dl>
      {quality.mismatches.length > 0 && <details>
        <summary>Divergências locais ({quality.mismatches.length})</summary>
        <ul>
          {quality.mismatches.map((item, index) => <li key={`${item.expected}:${item.observed}:${index}`}>
            {item.expected} → {item.observed}{item.occurrences > 1 ? ` (${item.occurrences}x)` : ""}
          </li>)}
        </ul>
        <small>Diagnóstico para revisão humana; nenhuma regra é promovida automaticamente.</small>
      </details>}
      {quality.chunks.length > 0 && <details>
        <summary>Diagnóstico por chunk ({quality.chunks.length})</summary>
        <table>
          <thead><tr><th>#</th><th>Classificação</th><th>WER</th><th>Cobertura estrutural</th></tr></thead>
          <tbody>{quality.chunks.map((chunk) => <tr key={chunk.index}>
            <td>{chunk.index + 1}</td><td>{chunk.classification}</td>
            <td>{chunk.wer}</td><td>{chunk.structuralCoverage}</td>
          </tr>)}</tbody>
        </table>
      </details>}
      {quality.asrTranscript && <details>
        <summary>Transcrição ASR</summary>
        <p>{quality.asrTranscript}</p>
      </details>}
    </details>
  </>;
}

function AccentQuality({ report }: { report: Record<string, unknown> | null }) {
  if (!report) return null;
  const classification = typeof report.classification === "string" ? report.classification : "uncertain";
  const status = report.detector_status === "available" ? classification : "detector unavailable";
  const reason = typeof report.reason_code === "string" ? ` (${report.reason_code})` : "";
  return <div>Accent: {status}{reason}</div>;
}

export default function WorkflowDetail() {
  const { id } = useParams<{ id: string }>();
  const workflowId = id ?? "";
  const navigate = useNavigate();
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [alignment, setAlignment] = useState<NarrationTimeline | null>(null);
  const [visualPlan, setVisualPlan] = useState<VisualPlan | null>(null);
  const [productionTimeline, setProductionTimeline] = useState<ProductionTimeline | null>(null);
  const [render, setRender] = useState<WorkflowRender | null>(null);
  const [qa, setQa] = useState<ProductionQAReport | null>(null);
  const [qaExecutando, setQaExecutando] = useState(false);
  const [chunkRefreshRevision, setChunkRefreshRevision] = useState(0);

  const carregar = useCallback(async (signal?: AbortSignal): Promise<boolean | void> => {
    if (!id) return;
    try {
      const wf = await api.obterWorkflow(id, signal);
      setWorkflow(wf);
      const phaseRank = wf.production_status === "completed"
        ? productionStages.length
        : productionStages.findIndex(([phase]) => phase === wf.production_phase);
      const currentAlignment = phaseRank >= 1
        ? await optionalArtifact(() => api.obterNarrationTimeline(id, signal))
        : null;
      const currentVisualPlan = phaseRank >= 2
        ? await optionalArtifact(() => api.obterVisualPlan(id, signal))
        : null;
      const currentProductionTimeline = phaseRank >= 3
        ? await optionalArtifact(() => api.obterProductionTimeline(id, signal))
        : null;
      setAlignment(currentAlignment);
      setVisualPlan(currentVisualPlan);
      setProductionTimeline(currentProductionTimeline);
      let currentRender: WorkflowRender | null = null;
      let currentQa: ProductionQAReport | null = null;
      if (shouldPollRender(wf)) {
        try { currentRender = await api.obterRender(id, signal); }
        catch (error) { if (!isExpectedNotFound(error)) throw error; }
        setRender(currentRender);
      } else {
        setRender(null);
      }
      if (shouldPollQA(wf)) {
        try { currentQa = await api.obterQAReport(id, "standard", signal); }
        catch (error) { if (!isExpectedNotFound(error)) throw error; }
        setQa(currentQa);
      } else {
        setQa(null);
      }
      setErro(null);
      const terminal = ["failed", "blocked", "cancelled"].includes(wf.production_status || "")
        || (wf.production_status === "completed" && currentQa !== null);
      return !terminal;
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return false;
      setErro((e as Error).message);
      throw e;
    }
  }, [id]);

  const refreshWorkflow = useCallback(async () => {
    await carregar();
  }, [carregar]);

  const refreshPublishedChunks = useCallback(() => {
    setChunkRefreshRevision((value) => value + 1);
  }, []);

  const audioEdit = useAudioEditUpdate({
    workflowId,
    editRevision: workflow?.audio_edit_revision ?? 0,
    onRefresh: refreshWorkflow,
    onPublished: refreshPublishedChunks,
  });

  useEffect(() => {
    if (!id) return;
    return startSerialPolling((signal) => carregar(signal), {
      intervalMs: 3000,
      maxBackoffMs: 30_000,
    });
  }, [id, carregar]);

  if (erro) return <div style={{ color: "crimson", padding: "2rem" }}>Erro: {erro}</div>;
  if (!workflow) return <div style={{ padding: "2rem" }}>Carregando...</div>;

  async function executarQA(mode: "standard" | "deep", force: boolean) {
    if (!id) return;
    setQaExecutando(true);
    try {
      const result = await api.executarQA(id, mode, force);
      setQa(result.report);
      setErro(null);
    } catch (e) {
      setErro((e as Error).message);
    } finally {
      setQaExecutando(false);
    }
  }

  function actionKey(label: string) {
    const suffix = typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
    return `ideas-hub:${label}:${id}:${suffix}`;
  }

  async function retryBlock(order: number) {
    if (!id) return;
    try { await api.retryAudioBlock(id, order, actionKey("retry-block")); await carregar(); }
    catch (e) { setErro((e as Error).message); }
  }

  async function regenerateBlock(order: number) {
    if (!id || !window.confirm("Gerar um novo take para este bloco? O take atual será preservado no histórico.")) return;
    try { await api.regenerateAudioBlock(id, order, actionKey("regenerate-block")); await carregar(); }
    catch (e) { setErro((e as Error).message); }
  }

  async function regenerateNarration() {
    if (!id || !window.confirm("Regenerar toda a narração? Os takes e artefatos antigos serão preservados.")) return;
    try { await api.regenerateNarration(id, actionKey("regenerate-narration")); await carregar(); }
    catch (e) { setErro((e as Error).message); }
  }

  async function resumeProduction() {
    if (!id) return;
    try {
      setErro(null);
      await api.retomarProducao(id);
      await carregar();
    } catch (e) {
      setErro((e as Error).message);
    }
  }

  const statusColors: Record<string, string> = {
    agendada: "#6c757d",
    gerando_audio: "#007bff",
    audio_concluido: "#28a745",
    erro: "#dc3545",
    cancelada: "#ffc107",
  };

  const currentWorkflow = workflow;
  const currentStage = currentWorkflow.production_phase === "completed"
    ? productionStages.length
    : productionStages.findIndex(([phase]) => phase === currentWorkflow.production_phase);

  function stageState(index: number): string {
    if (currentWorkflow.production_status === null) return index === 0 && currentWorkflow.status === "audio_concluido" ? "completed" : "legacy";
    if (index < currentStage || currentWorkflow.production_status === "completed") return "completed";
    if (index > currentStage) return "not_started";
    if (currentWorkflow.production_status === "blocked") return "blocked";
    if (currentWorkflow.production_status === "failed") return "failed";
    if (currentWorkflow.production_status === "cancelled") return "cancelled";
    return "active";
  }

  function stageDetail(index: number): string | null {
    if (index === 0 && currentWorkflow.duracao_audio_segundos != null) {
      return `${currentWorkflow.duracao_audio_segundos.toFixed(1)}s de áudio físico`;
    }
    if (index === 1 && alignment) {
      const coverage = alignment.blocks.length
        ? alignment.blocks.reduce((sum, block) => sum + block.overall_coverage, 0)
          / alignment.blocks.length
        : null;
      return coverage == null
        ? `${alignment.unresolved_visual_queries} queries não resolvidas`
        : `coverage média ${(coverage * 100).toFixed(1)}% · ${alignment.unresolved_visual_queries} não resolvidas`;
    }
    if (index === 2 && visualPlan) {
      return `${visualPlan.resolved_queries}/${visualPlan.total_queries} resolvidas · ${visualPlan.required_gaps} gaps obrigatórios`;
    }
    if (index === 3 && productionTimeline) {
      return `coverage ${(productionTimeline.visual_coverage_ratio * 100).toFixed(1)}% · ${productionTimeline.gap_count} gaps`;
    }
    if (index === 4 && render) {
      return `${Math.round(render.progress * 100)}% · ${render.frames_rendered}/${render.total_frames ?? "?"} frames`;
    }
    return null;
  }

  return (
    <div style={{ fontFamily: "sans-serif", maxWidth: 1000, margin: "2rem auto", padding: "0 1rem" }}>
      <button onClick={() => navigate("/")} style={{ marginBottom: "1rem" }}>
        ← Voltar
      </button>

      <h1>Workflow {workflow.id.slice(0, 8)}...</h1>

      <section className="production-timeline" aria-label="Timeline de produção">
        {productionStages.map(([, label], index) => {
          const state = stageState(index);
          return <div className={`production-stage ${state}`} key={label}>
            <span>{state === "completed" ? "✓" : index + 1}</span>
            <strong>{label}</strong>
            <small>{state.replace("_", " ")}</small>
            <em>{index === currentStage
              && ["failed", "blocked"].includes(workflow.production_status || "")
              && workflow.production_error_message
              ? workflow.production_error_message
              : stageDetail(index)}</em>
          </div>;
        })}
        <div className={`production-stage ${qa ? (qa.publication_ready ? "completed" : qa.status) : "not_started"}`}>
          <span>{qa?.publication_ready ? "✓" : 6}</span><strong>QA</strong><small>{qa?.status || "not started"}</small>
        </div>
      </section>

      <PendingAudioEdits
        workflow={workflow}
        update={audioEdit.update}
        submitting={audioEdit.submitting}
        error={audioEdit.error}
        notice={audioEdit.notice}
        onApply={() => { void audioEdit.apply(workflow.audio_edit_revision); }}
      />

      <div style={{ marginBottom: "2rem", padding: "1rem", background: "#f5f5f5", borderRadius: "8px" }}>
        <p><strong>Status:</strong> <span style={{ color: statusColors[workflow.status] }}>{workflow.status}</span></p>
        <p><strong>Perfil de Produção:</strong> {workflow.perfil_producao || "—"}</p>
        <p><strong>Retries da execução:</strong> {workflow.tentativas}</p>
        <p><strong>Fase de áudio:</strong> {workflow.audio_phase}</p>
        <p><strong>Produção:</strong> {workflow.production_status || "legado / inativa"}</p>
        <p><strong>Fase de produção:</strong> {workflow.production_phase || "—"}</p>
        <p><strong>Duração física final:</strong> {workflow.duracao_audio_segundos ? `${workflow.duracao_audio_segundos.toFixed(1)}s` : "—"}</p>
        <p><strong>Formato final:</strong> {workflow.audio_final_sample_rate ? `${workflow.audio_final_sample_rate} Hz / ${workflow.audio_final_channels} canal / ${workflow.audio_final_subtype || "subtipo desconhecido"}` : "—"}</p>
        <p><strong>Integridade do artefato:</strong> {(workflow.audio_integrity?.status as string) || "não validada"}</p>

        {workflow.erro_mensagem && (
          <p style={{ color: "crimson" }}><strong>Erro:</strong> {workflow.erro_mensagem}</p>
        )}
        {["failed", "blocked"].includes(workflow.production_status || "")
          && workflow.production_error_message && (
          <p style={{ color: "crimson" }}><strong>Erro de produção:</strong> {workflow.production_error_category}: {workflow.production_error_message}</p>
        )}
        {(workflow.production_error_history?.length || 0) > 0 && (
          <details>
            <summary>Histórico de erros ({workflow.production_error_history?.length})</summary>
            <ul>
              {workflow.production_error_history?.map((error, index) => (
                <li key={`${error.occurred_at}-${index}`}>
                  <strong>{error.phase} / {error.category}</strong>: {error.message}
                  {error.recovered_at && <span> — recuperado em {error.recovery_phase}</span>}
                </li>
              ))}
            </ul>
          </details>
        )}

        {render && (
          <div style={{ marginTop: "1rem" }}>
            <p><strong>Render:</strong> {render.status} / {render.phase} ({Math.round(render.progress * 100)}%)</p>
            {render.total_frames && <p><strong>Frames:</strong> {render.frames_rendered}/{render.total_frames}</p>}
            {render.error && <p style={{ color: "crimson" }}>{render.error.category}: {render.error.message}</p>}
            {render.status === "completed" && (
              <a href={api.urlVideo(workflow.id)} download>Baixar vídeo final</a>
            )}
          </div>
        )}

        {workflow.production_status === "completed" && (
          <div style={{ marginTop: "1rem", borderTop: "1px solid #ddd", paddingTop: "1rem" }}>
            <h3>QA de produção</h3>
            <p><strong>Status:</strong> {qa?.status || "Ainda não executado"}</p>
            <p>
              <strong>Release:</strong>{" "}
              {!qa
                ? "Não avaliado"
                : qa.publication_ready
                  ? qa.status === "passed_with_warnings"
                    ? "Pronto para publicação — com avisos"
                    : "Pronto para publicação"
                  : qa.status === "blocked"
                    ? "QA bloqueado"
                    : "Não aprovado pelo QA"}
            </p>
            {qa && (
              <p>
                <strong>Checks:</strong> {qa.passed_count} aprovados, {qa.warning_count} avisos,
                {" "}{qa.failed_count} falhas, {qa.blocked_count} bloqueados
              </p>
            )}
            {qa && qa.checks.some((item) => item.result === "warning" || item.result === "failed" || item.result === "blocked") && (
              <details>
                <summary>Ver avisos e falhas</summary>
                <ul>
                  {qa.checks
                    .filter((item) => ["warning", "failed", "blocked"].includes(item.result))
                    .map((item) => <li key={item.code}><code>{item.code}</code>: {item.message}</li>)}
                </ul>
              </details>
            )}
            <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
              <button disabled={qaExecutando} onClick={() => executarQA("standard", Boolean(qa))}>
                {qa ? "Reexecutar QA" : "Executar QA"}
              </button>
              <button disabled={qaExecutando} onClick={() => executarQA("deep", true)}>
                Executar QA profundo
              </button>
            </div>
          </div>
        )}

        {workflow.total_blocos !== undefined && (
          <div style={{ marginTop: "1rem" }}>
            <p><strong>Progresso:</strong></p>
            <ul>
              <li>Total de blocos: {workflow.total_blocos}</li>
              <li>Concluídos: {workflow.blocos_concluidos}</li>
              <li>Pendentes: {workflow.blocos_pendentes}</li>
              <li>Gerando: {workflow.blocos_gerando}</li>
              <li>Com erro: {workflow.blocos_erro}</li>
            </ul>
          </div>
        )}

        <div style={{ marginTop: "1rem" }}>
          {workflow.status === "audio_concluido" && workflow.audio_available && (
            <div>
              <p><strong>Áudio Final:</strong></p>
              <audio key={workflow.audio_final_sha256 || "pending"} controls preload="metadata" src={api.urlAudio(workflow.id, workflow.audio_final_sha256)} style={{ width: "100%", maxWidth: "500px" }} />
            </div>
          )}

          {workflow.status === "erro" && (
            <button onClick={() => api.repetirWorkflow(workflow.id).then(() => carregar())}>
              Repetir Workflow
            </button>
          )}

          {workflow.production_status === "failed" && workflow.status !== "erro" && (
            <button onClick={() => api.repetirWorkflow(workflow.id).then(() => carregar())}>
              Repetir fase de produção
            </button>
          )}

          <button onClick={regenerateNarration}>Regenerar narração completa</button>

          {(
            workflow.production_status === "blocked"
            || workflow.production_status === "cancelled"
            || workflow.production_status === null
          ) && (
            <button onClick={resumeProduction}>
              Retomar produção
            </button>
          )}

          {(workflow.production_status === "queued" || workflow.production_status === "running") && (
            <button onClick={() => api.cancelarWorkflow(workflow.id).then(() => carregar())}>
              Cancelar
            </button>
          )}
        </div>
      </div>

      <h2>Blocos de Áudio</h2>
      {workflow.blocos && workflow.blocos.length > 0 ? (
        <div className="audio-block-table-wrapper">
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "2px solid #ccc" }}>
              <th style={{ padding: "8px" }}>#</th>
              <th style={{ padding: "8px" }}>Tipo</th>
              <th style={{ padding: "8px" }}>Status</th>
              <th style={{ padding: "8px" }}>Duração</th>
              <th style={{ padding: "8px" }}>Voz / geração</th>
              <th style={{ padding: "8px" }}>Seed</th>
              <th style={{ padding: "8px" }}>Takes / tentativa atual</th>
              <th style={{ padding: "8px" }}>Texto</th>
              <th style={{ padding: "8px" }}>Erro</th>
              <th style={{ padding: "8px" }}>Áudio</th>
              <th style={{ padding: "8px" }}>Chunks</th>
            </tr>
          </thead>
          <tbody>
            {workflow.blocos.map((bloco) => (
              <tr key={bloco.id} style={{ borderBottom: "1px solid #eee" }}>
                <td style={{ padding: "8px" }}>{bloco.ordem + 1}</td>
                <td style={{ padding: "8px" }}>{bloco.tipo}</td>
                <td style={{ padding: "8px" }}>
                  <span style={{ color: statusColors[bloco.status] || "#000" }}>
                    {bloco.status}
                  </span>
                </td>
                <td style={{ padding: "8px" }}>
                  {bloco.duracao_segundos ? `${bloco.duracao_segundos.toFixed(1)}s` : "—"}
                </td>
                <td style={{ padding: "8px" }}>
                  {bloco.voice_profile || "—"} / {bloco.generation_profile || "—"}
                </td>
                <td style={{ padding: "8px" }}>{bloco.resolved_seed ?? "—"}</td>
                <td style={{ padding: "8px" }}>
                  {bloco.generation_attempt + 1} / {bloco.takes?.length || 0}
                  <div style={{ display: "flex", gap: "0.25rem", marginTop: "0.25rem" }}>
                    {(bloco.status === "erro" || bloco.takes?.some((take) => take.status === "failed" && !take.is_current)) && <button onClick={() => retryBlock(bloco.ordem)}>Retry bloco</button>}
                    <button onClick={() => regenerateBlock(bloco.ordem)}>Novo take</button>
                  </div>
                  {bloco.takes?.length > 0 && <details style={{ marginTop: "0.25rem" }}>
                    <summary>Histórico</summary>
                    <ul>
                      {bloco.takes.map((take) => (
                        <li key={take.id}>
                          #{take.attempt_index} {take.kind} — {take.status}, seed {take.resolved_seed ?? take.requested_seed ?? "—"}
                          {take.error_category ? ` — ${take.error_category}: ${take.error_message || ""}` : ""}
                          {take.is_current ? " (atual)" : ""}
                          <TakeQualityDetails report={take.content_quality} />
                          <AccentQuality report={take.accent_quality} />
                          {take.synthesis_text ? <details>
                            <summary>Proveniência do take</summary>
                            <div>Canônico: {take.canonical_text || "—"}</div>
                            <div>Normalizado: {take.normalized_text || "—"}</div>
                            <div>Síntese: {take.synthesis_text}</div>
                            <div>Normalizador: {take.text_normalizer_version || "—"} / {take.text_normalizer_identity || "—"}</div>
                            <div>Lexicon: {take.pronunciation_glossary_identity || "—"}</div>
                            {take.pronunciation_replacements?.length ? (
                              <div>Pronúncias: {take.pronunciation_replacements.map(formatPronunciation).join(", ")}</div>
                            ) : null}
                            <div>Voz: {take.voice_profile || "—"} / {take.voice_profile_identity || "—"}</div>
                            <div>Clone: {metadataText(take.provider_metadata, "voice_clone_mode")}</div>
                            <div>Referência: {metadataText(take.provider_metadata, "voice_reference_id")} / {metadataText(take.provider_metadata, "voice_reference_sha256")}</div>
                            <div>Geração: {take.generation_profile || "—"} / {take.generation_profile_identity || "—"}</div>
                            <div>Modelo: {take.model_id || "—"} / {take.model_revision || "—"}</div>
                            <div>Seeds: solicitado {take.requested_seed ?? "—"}; resolvido {take.resolved_seed ?? "—"}; chunks {chunkSeeds(take.provider_metadata)}</div>
                            <div>Chunking: {take.voice_chunking_identity || take.chunking_identity || "—"}</div>
                            <div>Artifact: {take.audio_sha256 || "—"}; {take.audio_duration_seconds?.toFixed(3) ?? "—"}s; {take.audio_sample_rate ?? "—"} Hz; {take.audio_channels ?? "—"} canal(is); {take.audio_subtype || "—"}</div>
                          </details> : null}
                        </li>
                      ))}
                    </ul>
                  </details>}
                </td>
                <td style={{ padding: "8px", maxWidth: 260 }}>
                  <details><summary>Ver texto</summary>
                    <p><strong>Canônico:</strong> {bloco.texto}</p>
                    <p><strong>Normalizado:</strong> {bloco.normalized_text || "—"}</p>
                    <p><strong>Síntese:</strong> {bloco.synthesis_text || "—"}</p>
                    {bloco.pronunciation_replacements?.length ? (
                      <p><strong>Pronúncias:</strong> {bloco.pronunciation_replacements.map(formatPronunciation).join(", ")}</p>
                    ) : null}
                  </details>
                </td>
                <td style={{ padding: "8px", color: "crimson" }}>
                  {bloco.erro_mensagem || "—"}
                </td>
                <td style={{ padding: "8px" }}>
                  {bloco.status === "concluido" && bloco.audio_sha256 && (
                    <audio key={`${bloco.id}:${bloco.audio_sha256}`} controls preload="metadata" src={api.urlAudioBloco(workflow.id, bloco.id, bloco.audio_sha256)} style={{ height: 30 }} />
                  )}
                </td>
                <td style={{ padding: "8px" }}>
                  <BlockChunks
                    workflowId={workflow.id}
                    blocoId={bloco.id}
                    blocoStatus={bloco.status}
                    dirty={bloco.audio_edit_dirty}
                    refreshRevision={chunkRefreshRevision}
                    onWorkflowRefresh={refreshWorkflow}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      ) : (
        <p>Nenhum bloco encontrado.</p>
      )}
    </div>
  );
}
