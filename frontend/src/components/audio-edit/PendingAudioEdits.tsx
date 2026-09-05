import type { AudioEditUpdate, Workflow } from "../../api/client";

function statusMessage(update: AudioEditUpdate): string {
  if (update.status === "pending") return "Atualização aguardando worker…";
  if (update.status === "running") return "Atualizando narração e pipeline…";
  if (update.status === "completed") return "Narração atualizada e publicada com sucesso.";
  return "A atualização não foi concluída.";
}

export function PendingAudioEdits({
  workflow,
  update,
  submitting,
  error,
  notice,
  onApply,
}: {
  workflow: Workflow;
  update: AudioEditUpdate | null;
  submitting: boolean;
  error: string | null;
  notice: string | null;
  onApply: () => void;
}) {
  const pending = workflow.audio_edits_pending
    || workflow.audio_edit_revision > workflow.audio_materialized_revision;
  const updateRunning = update?.status === "pending" || update?.status === "running";
  const canApply = pending || update?.status === "failed";
  const visible = pending || update !== null || error !== null || notice !== null;
  if (!visible) return null;

  return (
    <section className={`pending-audio-edits ${update?.status ?? (pending ? "pending" : "completed")}`} aria-live="polite">
      <div>
        <h2>{pending ? "Edições de narração pendentes" : "Atualização da narração"}</h2>
        <p>
          Seleção: revisão <strong>{workflow.audio_edit_revision}</strong>
          {" · "}Publicada: revisão <strong>{workflow.audio_materialized_revision}</strong>
        </p>
        {pending && !updateRunning && (
          <p>Os takes escolhidos ainda não fazem parte do áudio final publicado.</p>
        )}
        {update && <p className="audio-edit-update-state"><strong>{statusMessage(update)}</strong></p>}
        {update?.status === "failed" && (
          <p className="audio-edit-error" role="alert">
            {update.error_stage ? `${update.error_stage}: ` : ""}
            {update.error_message || "Falha sem detalhe adicional."}
          </p>
        )}
        {error && <p className="audio-edit-error" role="alert">{error}</p>}
        {notice && <p className="audio-edit-notice" role="status">{notice}</p>}
      </div>
      {canApply && !updateRunning && (
        <button type="button" disabled={submitting} onClick={onApply}>
          {submitting
            ? "Iniciando atualização…"
            : update?.status === "failed"
              ? "Tentar novamente"
              : "Atualizar Pipeline"}
        </button>
      )}
    </section>
  );
}
