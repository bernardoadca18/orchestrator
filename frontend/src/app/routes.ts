export const workflowSections = [
  ["overview", "Visão geral"],
  ["narration", "Narração"],
  ["visuals", "Visuais e Timeline"],
  ["presentation", "Apresentação"],
  ["program-audio", "Program Audio"],
  ["render", "Render e QA"],
  ["thumbnails", "Thumbnails"],
  ["history", "Histórico"],
] as const;

export function workflowPath(workflowId: string, section = "overview") {
  return `/productions/${workflowId}/${section}`;
}

export function legacyWorkflowTarget(workflowId: string) {
  return workflowPath(workflowId, "overview");
}
