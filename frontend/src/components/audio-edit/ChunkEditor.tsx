import { useState } from "react";

import { api, AudioChunk, AudioChunkTake } from "../../api/client";
import { createAudioActionKey, formatAudioEditError } from "../../utils/audioEdit";

function duration(value: number | null): string {
  return value == null ? "duração indisponível" : `${value.toFixed(2)} s`;
}

function shortSha(value: string | null): string {
  return value ? value.slice(0, 12) : "—";
}

function takeLabel(take: AudioChunkTake): string {
  return `Take ${take.take_number} · ${take.kind} · ${take.status}`;
}

function accentLabel(report: Record<string, unknown> | null): string {
  if (!report) return "Accent: não medido";
  if (report.detector_status !== "available") return "Accent: detector indisponível";
  if (report.classification === "pt_br_confident") return "Accent: PT-BR confident";
  if (report.classification === "likely_pt_pt") return "Accent: likely PT-PT";
  return "Accent: uncertain";
}

function TakeAudio({ workflowId, take }: { workflowId: string; take: AudioChunkTake }) {
  if (!take.audio_url || !take.audio_sha256) {
    return <span className="audio-edit-muted">Áudio indisponível</span>;
  }
  return (
    <audio
      key={`${take.id}:${take.audio_sha256}`}
      controls
      preload="metadata"
      src={api.urlChunkTakeAudio(workflowId, take.id, take.audio_sha256)}
    />
  );
}

export function ChunkEditor({
  workflowId,
  blocoId,
  chunk,
  onChanged,
}: {
  workflowId: string;
  blocoId: string;
  chunk: AudioChunk;
  onChanged: () => Promise<void>;
}) {
  const [regenerating, setRegenerating] = useState(false);
  const [regenerationKey, setRegenerationKey] = useState<string | null>(null);
  const [takeAction, setTakeAction] = useState<{ id: string; action: "promote" | "discard" } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const activeTake = chunk.takes.find((take) => take.id === chunk.current_take_id);
  const candidateTakes = chunk.takes.filter((take) => (
    take.kind === "regeneration" && ["candidate", "queued", "running"].includes(take.status)
  ));
  const candidateIds = new Set(candidateTakes.map((take) => take.id));
  const historyTakes = chunk.takes.filter((take) => (
    take.id !== chunk.current_take_id && !candidateIds.has(take.id)
  ));
  const busy = regenerating || takeAction !== null;

  async function handleRegenerate() {
    if (busy) return;
    const key = regenerationKey ?? createAudioActionKey(
      "chunk-regenerate",
      workflowId,
      chunk.id,
    );
    setRegenerationKey(key);
    setRegenerating(true);
    setError(null);
    setNotice(null);
    try {
      await api.regenerarChunk(workflowId, blocoId, chunk.chunk_index, key);
      setRegenerationKey(null);
      setNotice("Novo take pronto para comparação. O take atual não foi alterado.");
      await onChanged();
    } catch (cause) {
      setError(formatAudioEditError(cause, "Não foi possível regenerar este trecho."));
    } finally {
      setRegenerating(false);
    }
  }

  async function handlePromote(takeId: string) {
    if (busy) return;
    setTakeAction({ id: takeId, action: "promote" });
    setError(null);
    setNotice(null);
    try {
      const result = await api.promoverChunkTake(workflowId, takeId);
      setNotice(result.message);
      await onChanged();
    } catch (cause) {
      setError(formatAudioEditError(cause, "Não foi possível selecionar este take."));
    } finally {
      setTakeAction(null);
    }
  }

  async function handleDiscard(takeId: string) {
    if (busy || !window.confirm("Descartar este novo take? Ele continuará disponível no histórico.")) return;
    setTakeAction({ id: takeId, action: "discard" });
    setError(null);
    setNotice(null);
    try {
      await api.descartarChunkTake(workflowId, takeId);
      setNotice("Take descartado e preservado no histórico. O take atual não mudou.");
      await onChanged();
    } catch (cause) {
      setError(formatAudioEditError(cause, "Não foi possível descartar este take."));
    } finally {
      setTakeAction(null);
    }
  }

  return (
    <article className="audio-chunk-card">
      <header className="audio-chunk-header">
        <div>
          <strong>Trecho {chunk.chunk_index + 1}</strong>
          <span className="audio-edit-badge">{chunk.status}</span>
          <div className="audio-edit-meta">{duration(chunk.duration_seconds)} · seed original {chunk.seed}</div>
        </div>
        <button type="button" disabled={busy} onClick={handleRegenerate}>
          {regenerating ? "Regenerando…" : regenerationKey && error ? "Tentar regenerar novamente" : "Regenerar"}
        </button>
      </header>

      <div className="audio-chunk-text">
        <p><strong>Texto:</strong> {chunk.canonical_text}</p>
        {chunk.synthesis_text_snapshot !== chunk.canonical_text && (
          <p><strong>Texto de síntese:</strong> {chunk.synthesis_text_snapshot}</p>
        )}
      </div>

      {error && <p className="audio-edit-error" role="alert">{error}</p>}
      {notice && <p className="audio-edit-notice" role="status">{notice}</p>}

      <section className="audio-take-current" aria-label={`Take atual do trecho ${chunk.chunk_index + 1}`}>
        <div>
          <strong>Take atual</strong>
          {activeTake && <span className="audio-edit-meta"> {takeLabel(activeTake)}</span>}
        </div>
        {activeTake ? (
          <>
            <TakeAudio workflowId={workflowId} take={activeTake} />
            <div className="audio-edit-meta">
              {duration(activeTake.audio_duration_seconds)} · seed {activeTake.seed} · SHA {shortSha(activeTake.audio_sha256)}
            </div>
            <div className="audio-edit-meta">{accentLabel(activeTake.accent_quality)}</div>
          </>
        ) : <p className="audio-edit-error">Nenhum take atual selecionado.</p>}
      </section>

      {candidateTakes.map((take) => (
        <section className="audio-take-candidate" key={take.id} aria-label={`Novo take ${take.take_number}`}>
          <div>
            <strong>Novo take</strong>
            <span className="audio-edit-badge">{take.status}</span>
          </div>
          <TakeAudio workflowId={workflowId} take={take} />
          <div className="audio-edit-meta">
            {duration(take.audio_duration_seconds)} · seed {take.seed} · SHA {shortSha(take.audio_sha256)}
          </div>
          <div className="audio-edit-meta">{accentLabel(take.accent_quality)}</div>
          {take.error_message && <p className="audio-edit-error">{take.error_message}</p>}
          {take.status === "candidate" && (
            <div className="audio-edit-actions">
              <button
                type="button"
                disabled={busy}
                onClick={() => handlePromote(take.id)}
              >
                {takeAction?.id === take.id && takeAction.action === "promote" ? "Selecionando…" : "Usar este take"}
              </button>
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={() => handleDiscard(take.id)}
              >
                {takeAction?.id === take.id && takeAction.action === "discard" ? "Descartando…" : "Descartar"}
              </button>
            </div>
          )}
        </section>
      ))}

      {historyTakes.length > 0 && (
        <details className="audio-take-history">
          <summary>Histórico de takes ({historyTakes.length})</summary>
          {historyTakes.map((take) => (
            <div className="audio-take-history-row" key={take.id}>
              <div><strong>Take {take.take_number}</strong> · {take.kind} · {take.status}</div>
              <TakeAudio workflowId={workflowId} take={take} />
              <div className="audio-edit-meta">
                {duration(take.audio_duration_seconds)} · seed {take.seed} · {take.created_at ? new Date(take.created_at).toLocaleString("pt-BR") : "data indisponível"}
              </div>
              <div className="audio-edit-meta">{accentLabel(take.accent_quality)}</div>
              {take.error_message && <p className="audio-edit-error">{take.error_message}</p>}
            </div>
          ))}
        </details>
      )}

      <details className="audio-edit-advanced">
        <summary>Detalhes avançados</summary>
        <dl>
          <div><dt>Chunk durável</dt><dd>{chunk.id}</dd></div>
          <div><dt>Chunk de origem</dt><dd>{chunk.chunk_id}</dd></div>
          <div><dt>Boundary</dt><dd>{chunk.boundary}</dd></div>
          <div><dt>Pausa resolvida</dt><dd>{chunk.pause_after_frames ?? "—"} frames</dd></div>
          <div><dt>Sample rate</dt><dd>{chunk.sample_rate ?? "—"} Hz</dd></div>
          <div><dt>Take atual</dt><dd>{chunk.current_take_id ?? "—"}</dd></div>
        </dl>
      </details>
    </article>
  );
}
