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
    let active = true;
    return startSerialPolling(async (signal) => {
      const snapshot = await operatorApi.getWorkflow(workflowId, signal);
      active = ["queued", "running"].includes(snapshot.workflow.production_status || "");
      setData(snapshot);
      setError(null);
    }, {
      intervalMs: () => active ? 4_000 : 60_000,
      maxBackoffMs: 120_000,
      onError: (reason) => setError(reason instanceof Error ? reason.message : "Falha ao atualizar produção"),
    });
  }, [workflowId, refreshRevision]);

  return { data, error, refresh };
}
