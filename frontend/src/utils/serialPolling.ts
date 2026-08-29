export interface SerialPollingOptions {
  intervalMs: number;
  maxBackoffMs?: number;
  onError?: (error: unknown) => void;
}

export function pollingDelayMs(
  intervalMs: number,
  maxBackoffMs: number,
  failures: number,
): number {
  return Math.min(intervalMs * 2 ** failures, maxBackoffMs);
}

/** Run one request at a time, with cancellation and bounded transient backoff. */
export function startSerialPolling(
  task: (signal: AbortSignal) => Promise<void | boolean>,
  options: SerialPollingOptions,
): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let failures = 0;
  let controller: AbortController | null = null;
  const maxBackoff = options.maxBackoffMs ?? options.intervalMs * 8;

  const run = async () => {
    if (stopped) return;
    controller = new AbortController();
    try {
      const keepPolling = await task(controller.signal);
      failures = 0;
      if (keepPolling === false) {
        stopped = true;
        return;
      }
    } catch (error) {
      if (controller.signal.aborted || stopped) return;
      failures += 1;
      options.onError?.(error);
    }
    if (stopped) return;
    const delay = pollingDelayMs(options.intervalMs, maxBackoff, failures);
    timer = setTimeout(() => { void run(); }, delay);
  };

  void run();
  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    controller?.abort();
  };
}
