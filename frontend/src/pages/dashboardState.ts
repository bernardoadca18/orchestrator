export function isActiveProductionStatus(status: string | null | undefined) {
  return status === "queued" || status === "running";
}
