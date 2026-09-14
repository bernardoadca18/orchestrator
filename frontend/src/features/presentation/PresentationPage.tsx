import { useCallback } from "react";
import { ComposerInput, operatorApi } from "../../api/operator";
import { Card, DefinitionList, Diagnostics, EmptyState, LoadingState, StatusBadge, TableWrap } from "../../components/ui";
import { useColdArtifact } from "../../hooks/useColdArtifact";
import { useWorkflowContext } from "../workflow/WorkflowWorkspace";

export default function PresentationPage() {
  const { snapshot } = useWorkflowContext();
  const id = snapshot.workflow.id;
  const identity = [snapshot.artifacts.production_timeline?.fingerprint, snapshot.artifacts.presentation_fingerprint, snapshot.artifacts.audio_mix_plan_fingerprint].filter(Boolean).join(":");
  const loader = useCallback((signal?: AbortSignal) => operatorApi.getComposerInput(id, signal), [id]);
  const result = useColdArtifact<ComposerInput>(id, "composer-input", identity, loader);
  if (result.loading) return <LoadingState label="Carregando PresentationSpec…" />;
  if (!result.data?.presentation) return <EmptyState title="PresentationSpec ainda não disponível" detail={result.error || "O frontend não regenera este contrato."} />;
  const spec = result.data.presentation;
  const cues = spec.captions.cues;
  return <div className="workspace-stack"><Card title="PresentationSpec">
    <DefinitionList items={[["Schema", spec.schema_version], ["Estratégia", spec.strategy_version], ["Profile", spec.profile_id], ["Fingerprint", <code>{spec.fingerprint}</code>], ["Branding", <><StatusBadge value={spec.branding.enabled ? "enabled" : "disabled"} /> {spec.branding.preset}</>]]} />
  </Card>
  <Card title={`Captions (${cues.length})`}><DefinitionList items={[["Policy", spec.captions.policy_version], ["Preset", spec.captions.preset], ["Primeiro cue", cues[0] ? `${cues[0].start_seconds.toFixed(2)}s · ${cues[0].text}` : "—"], ["Último cue", cues.at(-1) ? `${cues.at(-1)?.end_seconds.toFixed(2)}s · ${cues.at(-1)?.text}` : "—"]]} /><details><summary>Todos os cues</summary><TableWrap><table><thead><tr><th>Range</th><th>Texto</th></tr></thead><tbody>{cues.map((cue, index) => <tr key={`${cue.start_seconds}-${index}`}><td>{cue.start_seconds.toFixed(2)}–{cue.end_seconds.toFixed(2)}s</td><td>{cue.text}</td></tr>)}</tbody></table></TableWrap></details></Card>
  <Card title={`Section titles (${spec.section_titles.length})`}>{spec.section_titles.length ? <TableWrap><table><thead><tr><th>Range</th><th>Texto</th><th>Preset</th></tr></thead><tbody>{spec.section_titles.map((item, index) => <tr key={`${item.start_seconds}-${index}`}><td>{item.start_seconds.toFixed(2)}–{item.end_seconds.toFixed(2)}s</td><td>{item.text}</td><td>{item.preset}</td></tr>)}</tbody></table></TableWrap> : <EmptyState title="Sem section titles" />}</Card>
  <Card title={`Transitions (${spec.transitions.length})`}>{spec.transitions.length ? <TableWrap><table><thead><tr><th>Boundary</th><th>Tipo</th><th>Duração</th><th>Preset</th></tr></thead><tbody>{spec.transitions.map((item, index) => <tr key={`${item.boundary_seconds}-${index}`}><td>{item.boundary_seconds.toFixed(2)}s</td><td>{item.type}</td><td>{item.duration_seconds.toFixed(2)}s</td><td>{item.preset}</td></tr>)}</tbody></table></TableWrap> : <EmptyState title="Sem transitions" />}</Card>
  <Diagnostics label="Branding completo" value={spec.branding} /></div>;
}
