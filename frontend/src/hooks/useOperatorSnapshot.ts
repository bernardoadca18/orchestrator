import { useCallback, useEffect, useState } from "react";
import { operatorApi, OperatorWorkflowSnapshot } from "../api/operator";
import { startSerialPolling } from "../utils/serialPolling";

export function useOperatorSnapshot(workflowId: string) {
  const [data, setData] = useState<OperatorWorkflowSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshRevision, setRefreshRevision] = useState(0);

  const refresh = useCallback(() => setRefreshRevision((value) => value + 1), []);

  useEffect(() => {
    if (!workflowId) return;
    return startSerialPolling(async (signal) => {
      const snapshot = await operatorApi.getWorkflow(workflowId, signal);
      setData(snapshot);
      setError(null);
    }, {
      intervalMs: 4_000,
      maxBackoffMs: 30_000,
      onError: (reason) => setError(reason instanceof Error ? reason.message : "Falha ao atualizar produção"),
    });
  }, [workflowId, refreshRevision]);

  return { data, error, refresh };
}
