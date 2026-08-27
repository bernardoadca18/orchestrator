export interface ArtifactPollingState {
  production_status: string | null;
  production_phase: string | null;
}

/** Render/QA endpoints intentionally return 404 until their phase is reached. */
export function shouldPollRender(workflow: ArtifactPollingState): boolean {
  return workflow.production_phase === "render" || workflow.production_status === "completed";
}

export function shouldPollQA(workflow: ArtifactPollingState): boolean {
  return workflow.production_status === "completed";
}
