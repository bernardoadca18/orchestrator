import { useCallback, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { SingleFlightAction } from "../data/operatorAction";

export function useOperatorAction(onSuccess?: () => void) {
  const flight = useRef(new SingleFlightAction());
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const run = useCallback(
    async <T>(operation: () => Promise<T>, success: string) => {
      setSubmitting(true);
      setNotice(null);
      setError(null);
      setConflict(false);
      try {
        const result = await flight.current.run(operation);
        setNotice(success);
        onSuccess?.();
        return result;
      } catch (reason) {
        const isConflict = reason instanceof ApiError && reason.status === 409;
        setConflict(isConflict);
        setError(
          reason instanceof Error ? reason.message : "A operação falhou.",
        );
        return null;
      } finally {
        setSubmitting(false);
      }
    },
    [onSuccess],
  );

  return {
    run,
    submitting,
    error,
    conflict,
    notice,
    clearNotice: () => setNotice(null),
  };
}
