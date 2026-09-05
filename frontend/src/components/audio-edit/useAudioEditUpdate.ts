import { useEffect, useState } from "react";

import { api, AudioEditUpdate } from "../../api/client";
import { shouldPollAudioEditUpdate } from "../../pages/polling";
import { createAudioActionKey, formatAudioEditError } from "../../utils/audioEdit";
import { startSerialPolling } from "../../utils/serialPolling";

interface StoredAudioEditOperation {
  updateId: string;
  actionKey: string;
}

function storageKey(workflowId: string): string {
  return `neovids:audio-edit-update:${workflowId}`;
}

function loadStoredOperation(workflowId: string): StoredAudioEditOperation | null {
  try {
    const value = sessionStorage.getItem(storageKey(workflowId));
    if (!value) return null;
    const parsed = JSON.parse(value) as Partial<StoredAudioEditOperation>;
    return typeof parsed.updateId === "string" && typeof parsed.actionKey === "string"
      ? { updateId: parsed.updateId, actionKey: parsed.actionKey }
      : null;
  } catch {
    return null;
  }
}

function storeOperation(workflowId: string, operation: StoredAudioEditOperation | null) {
  try {
    if (operation) {
      sessionStorage.setItem(storageKey(workflowId), JSON.stringify(operation));
    } else {
      sessionStorage.removeItem(storageKey(workflowId));
    }
  } catch {
    // The durable backend operation remains authoritative when browser storage is unavailable.
  }
}

export function useAudioEditUpdate({
  workflowId,
  editRevision,
  onRefresh,
  onPublished,
}: {
  workflowId: string;
  editRevision: number;
  onRefresh: () => Promise<void>;
  onPublished: () => void;
}) {
  const [updateId, setUpdateId] = useState<string | null>(null);
  const [actionKey, setActionKey] = useState<string | null>(null);
  const [update, setUpdate] = useState<AudioEditUpdate | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [pollAttempt, setPollAttempt] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    const stored = loadStoredOperation(workflowId);
    setUpdateId(stored?.updateId ?? null);
    setActionKey(stored?.actionKey ?? null);
    setUpdate(null);
    setError(null);
    setNotice(null);
  }, [workflowId]);

  useEffect(() => {
    if (
      update
      && !shouldPollAudioEditUpdate(update.status)
      && editRevision > update.target_revision
    ) {
      setUpdateId(null);
      setUpdate(null);
      setActionKey(null);
      setError(null);
      setNotice(null);
      storeOperation(workflowId, null);
    }
  }, [workflowId, editRevision, update]);

  useEffect(() => {
    if (!updateId) return;
    return startSerialPolling(async (signal) => {
      const current = await api.obterAudioEditUpdate(workflowId, updateId, signal);
      setUpdate(current);
      setActionKey(current.idempotency_key);
      setError(null);
      storeOperation(workflowId, {
        updateId: current.id,
        actionKey: current.idempotency_key,
      });
      if (shouldPollAudioEditUpdate(current.status)) return true;
      if (current.status === "completed") {
        storeOperation(workflowId, null);
        setActionKey(null);
        setNotice("Narração atualizada e publicada. O estado da produção foi recarregado separadamente.");
        await onRefresh();
        onPublished();
      }
      return false;
    }, {
      intervalMs: 2_000,
      maxBackoffMs: 20_000,
      onError: () => setError("Status temporariamente indisponível; uma nova tentativa será feita automaticamente."),
    });
  }, [workflowId, updateId, pollAttempt, onRefresh, onPublished]);

  async function apply(revision: number) {
    if (submitting || update?.status === "pending" || update?.status === "running") return;
    const key = actionKey ?? createAudioActionKey(
      "audio-edit-apply",
      workflowId,
      String(revision),
    );
    setActionKey(key);
    setSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.aplicarEdicoesAudio(workflowId, key);
      if (result.status === "no_pending" || result.update_id === null) {
        setUpdateId(null);
        setUpdate(null);
        setActionKey(null);
        storeOperation(workflowId, null);
        setNotice("Não há edições de narração pendentes para publicar.");
        await onRefresh();
        return;
      }
      setUpdate(null);
      setUpdateId(result.update_id);
      storeOperation(workflowId, { updateId: result.update_id, actionKey: key });
      setPollAttempt((value) => value + 1);
    } catch (cause) {
      setError(formatAudioEditError(cause, "Não foi possível iniciar a atualização do pipeline."));
    } finally {
      setSubmitting(false);
    }
  }

  return {
    update,
    submitting: submitting || (updateId !== null && update === null),
    error,
    notice,
    apply,
  };
}
