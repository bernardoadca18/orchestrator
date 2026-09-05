export function createAudioActionKey(
  action: "chunk-regenerate" | "audio-edit-apply",
  workflowId: string,
  subject: string,
): string {
  return `${action}:${workflowId}:${subject}:${crypto.randomUUID()}`;
}

export function formatAudioEditError(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}
