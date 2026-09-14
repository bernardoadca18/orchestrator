import { useEffect, useState } from "react";
import { isExpectedNotFound } from "../api/client";
import { coldArtifactCache, ColdLoader } from "../data/coldArtifactCache";

export function useColdArtifact<T>(
  workflowId: string,
  resource: string,
  identity: string | null | undefined,
  loader: ColdLoader<T>,
) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(Boolean(identity));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!identity) {
      setData(null);
      setLoading(false);
      setError(null);
      return;
    }
    let disposed = false;
    setLoading(true);
    // The cache owns cold requests. A StrictMode remount or section navigation must
    // not abort the shared promise for the next consumer of the same fingerprint.
    coldArtifactCache.load(`${workflowId}:${resource}`, identity, loader)
      .then((value) => {
        if (!disposed) {
          setData(value);
          setError(null);
        }
      })
      .catch((reason) => {
        if (disposed) return;
        if (isExpectedNotFound(reason)) {
          setData(null);
          setError(null);
        } else {
          setError(reason instanceof Error ? reason.message : "Falha ao carregar artefato");
        }
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => { disposed = true; };
  }, [workflowId, resource, identity, loader]);

  return { data, loading, error };
}
