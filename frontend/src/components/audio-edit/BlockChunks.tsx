import { useEffect, useState } from "react";

import { api, AudioChunk } from "../../api/client";
import { formatAudioEditError } from "../../utils/audioEdit";
import { ChunkEditor } from "./ChunkEditor";

export function BlockChunks({
  workflowId,
  blocoId,
  blocoStatus,
  dirty,
  refreshRevision,
  onWorkflowRefresh,
}: {
  workflowId: string;
  blocoId: string;
  blocoStatus: string;
  dirty: boolean;
  refreshRevision: number;
  onWorkflowRefresh: () => Promise<void>;
}) {
  const [chunks, setChunks] = useState<AudioChunk[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!expanded) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    api.listarChunks(workflowId, blocoId, controller.signal)
      .then(setChunks)
      .catch((cause) => {
        if (cause instanceof DOMException && cause.name === "AbortError") return;
        setError(formatAudioEditError(cause, "Não foi possível carregar os chunks."));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [expanded, workflowId, blocoId, refreshRevision]);

  async function refreshAfterAction() {
    setLoading(true);
    setError(null);
    try {
      const [freshChunks] = await Promise.all([
        api.listarChunks(workflowId, blocoId),
        onWorkflowRefresh(),
      ]);
      setChunks(freshChunks);
    } catch (cause) {
      setError(formatAudioEditError(cause, "A ação terminou, mas não foi possível atualizar os dados."));
    } finally {
      setLoading(false);
    }
  }

  if (blocoStatus !== "concluido") return null;

  return (
    <details
      className="block-chunks"
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary>
        Chunks físicos {chunks ? `(${chunks.length})` : ""}
        {dirty && <span className="audio-edit-badge pending">edição pendente</span>}
      </summary>
      {error && <p className="audio-edit-error" role="alert">{error}</p>}
      {loading && !chunks ? (
        <p className="audio-edit-muted">Carregando chunks…</p>
      ) : chunks && chunks.length > 0 ? (
        <div className="audio-chunk-list">
          {chunks.map((chunk) => (
            <ChunkEditor
              key={chunk.id}
              workflowId={workflowId}
              blocoId={blocoId}
              chunk={chunk}
              onChanged={refreshAfterAction}
            />
          ))}
        </div>
      ) : !loading ? (
        <p className="audio-edit-muted">Nenhum chunk físico persistido.</p>
      ) : null}
    </details>
  );
}
