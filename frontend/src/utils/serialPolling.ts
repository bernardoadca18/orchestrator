export interface SerialPollingOptions {
  intervalMs: number | (() => number);
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
  let running = false;
  const visible = () => typeof document === "undefined" || !document.hidden;
  const interval = () => typeof options.intervalMs === "function" ? options.intervalMs() : options.intervalMs;

  const run = async () => {
    if (stopped || running || !visible()) return;
    running = true;
    controller = new AbortController();
    try {
      const keepPolling = await task(controller.signal);
      failures = 0;
      if (keepPolling === false) {
        stopped = true;
        if (typeof document !== "undefined") document.removeEventListener("visibilitychange", onVisibility);
        return;
      }
    } catch (error) {
      if (controller.signal.aborted || stopped) return;
      failures += 1;
      options.onError?.(error);
    } finally {
      running = false;
      controller = null;
    }
    if (stopped || !visible()) return;
    const base = interval();
    const delay = pollingDelayMs(base, options.maxBackoffMs ?? base * 8, failures);
    timer = setTimeout(() => { void run(); }, delay);
  };

  const onVisibility = () => {
    if (stopped || !visible()) return;
    if (timer) clearTimeout(timer);
    timer = null;
    void run();
  };
  if (typeof document !== "undefined") document.addEventListener("visibilitychange", onVisibility);
  void run();
  return () => {
    stopped = true;
    if (typeof document !== "undefined") document.removeEventListener("visibilitychange", onVisibility);
    if (timer) clearTimeout(timer);
    controller?.abort();
  };
}
