export function createActionKey(action: string, workflowId: string): string {
  const suffix = typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `operator-console:${action}:${workflowId}:${suffix}`;
}

export class SingleFlightAction {
  private running = false;

  get submitting() {
    return this.running;
  }

  async run<T>(operation: () => Promise<T>): Promise<T> {
    if (this.running) throw new Error("Esta ação já está em andamento.");
    this.running = true;
    try {
      return await operation();
    } finally {
      this.running = false;
    }
  }
}
