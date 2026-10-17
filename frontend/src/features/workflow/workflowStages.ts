export const videoStages = [
  ["audio", "Narração"],
  ["narration_alignment", "Sincronização"],
  ["visual_plan", "Plano visual"],
  ["production_timeline", "Timeline"],
  ["render", "Renderização"],
] as const;

export const storyboardStages = [
  ["audio", "Narração"],
  ["narration_alignment", "Sincronização"],
  ["storyboard", "Storyboard"],
  ["storyboard_assets", "Imagens"],
  ["production_timeline", "Timeline"],
  ["render", "Renderização"],
] as const;

export const manualStages = [
  ["audio", "Narração"],
  ["manual_edit_package", "Pacote para edição"],
] as const;

export function productionStages(
  phase: string | null,
  storyboard = false,
  manualPackage = false,
) {
  return manualPackage || phase === "manual_edit_package"
    ? manualStages
    : storyboard
      ? storyboardStages
      : videoStages;
}

export function sectionForStage(stage: string) {
  if (["audio", "narration_alignment"].includes(stage)) return "narration";
  if (["visual_plan", "production_timeline"].includes(stage)) return "visuals";
  if (stage === "render" || stage === "qa") return "render";
  // Storyboard and manual delivery are supported inside Overview.
  return "overview";
}

export function stagePresentation(
  phase: string | null,
  status: string | null,
  stage: string,
  storyboard = false,
  manualPackage = false,
) {
  const stages = productionStages(
    phase,
    storyboard,
    manualPackage || stage === "manual_edit_package",
  );
  const current = stages.findIndex(([key]) => key === phase);
  const index = stages.findIndex(([key]) => key === stage);
  // The backend uses this explicit terminal phase after the final production step.
  if (phase === "completed" && status === "completed" && index >= 0)
    return "completed";
  // An unfamiliar phase is not evidence that every stage succeeded.
  if (current < 0 || index < 0) return "unknown";
  if (index < current) return "completed";
  if (index > current)
    return status === "completed" ? "unknown" : "not_started";
  if (
    [
      "completed",
      "failed",
      "blocked",
      "cancelled",
      "queued",
      "pending",
    ].includes(status || "")
  )
    return status!;
  return status === "running" ? "active" : "unknown";
}
