export type ColdLoader<T> = (signal?: AbortSignal) => Promise<T>;

interface CacheEntry<T> {
  identity: string;
  value?: T;
  promise?: Promise<T>;
}

export class ColdArtifactCache {
  private entries = new Map<string, CacheEntry<unknown>>();

  load<T>(key: string, identity: string, loader: ColdLoader<T>, signal?: AbortSignal): Promise<T> {
    const existing = this.entries.get(key) as CacheEntry<T> | undefined;
    if (existing?.identity === identity && existing.value !== undefined) {
      return Promise.resolve(existing.value);
    }
    if (existing?.identity === identity && existing.promise) return existing.promise;
    const entry: CacheEntry<T> = { identity };
    entry.promise = loader(signal).then((value) => {
      if (this.entries.get(key) === entry) {
        entry.value = value;
        delete entry.promise;
      }
      return value;
    }).catch((error) => {
      if (this.entries.get(key) === entry) this.entries.delete(key);
      throw error;
    });
    this.entries.set(key, entry);
    return entry.promise;
  }

  invalidate(key: string) {
    this.entries.delete(key);
  }

  invalidateWorkflow(workflowId: string, resources: string[] = []) {
    for (const key of this.entries.keys()) {
      if (!key.startsWith(`${workflowId}:`)) continue;
      if (resources.length === 0 || resources.some((resource) => key === `${workflowId}:${resource}`)) {
        this.entries.delete(key);
      }
    }
  }

  clear() {
    this.entries.clear();
  }
}

export const coldArtifactCache = new ColdArtifactCache();
