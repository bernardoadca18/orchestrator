import { useCallback } from "react";
import { operatorApi, ProductionTimelineDetail, VisualPlanDetail, VisualSource } from "../../api/operator";
import { Alert, Card, DefinitionList, Diagnostics, EmptyState, LoadingState, ScoreBar, StatusBadge, TableWrap } from "../../components/ui";
import { useColdArtifact } from "../../hooks/useColdArtifact";
import { useWorkflowContext } from "../workflow/WorkflowWorkspace";

function Source({ source, selected = false }: { source: VisualSource; selected?: boolean }) {
  return <div className={`source-details ${selected ? "source-selected" : "source-alternative"}`}>
    <strong>{source.media_filename}</strong> <StatusBadge value={source.source_type} />
    <div className="source-scores">
      <ScoreBar value={source.selection_score} label="Seleção" />
      {source.semantic_score != null && <ScoreBar value={source.semantic_score} label="Semântica" />}
    </div>
    <dl className="definition-list compact">
      <div><dt>Rank</dt><dd>{source.rank ?? "—"}</dd></div>
      {source.source_type !== "static_image" ? <div><dt>Range</dt><dd>{source.source_start_seconds?.toFixed(2)}–{source.source_end_seconds?.toFixed(2)}s · {source.session_label}</dd></div> : <div><dt>Imagem</dt><dd>{source.width}×{source.height}</dd></div>}
    </dl>
    {source.transcript_preview && <p><strong>ASR:</strong> {source.transcript_preview}</p>}
    {source.semantic_preview && <p><strong>Semântica:</strong> {source.semantic_preview}</p>}
    <Diagnostics label="Proveniência" value={source.provenance} />
  </div>;
}

function Timeline({ timeline }: { timeline: ProductionTimelineDetail }) {
  const duration = Math.max(timeline.duration_seconds, 0.001);
  return <Card title="Production Timeline">
    <DefinitionList items={[
      ["Status", <StatusBadge value={timeline.status} />],
      ["Cobertura", `${(timeline.visual_coverage_ratio * 100).toFixed(1)}%`],
      ["Gaps", `${timeline.gap_count} · obrigatório ${timeline.required_gap_seconds.toFixed(2)}s`],
      ["Schema / estratégia", `${timeline.schema_version} / ${timeline.strategy_version}`],
    ]} />
    <div className="linear-timeline" aria-label="Representação linear da Production Timeline">
      {timeline.tracks.primary_visual.placements.map((item) => <div className="timeline-segment" key={item.id} title={`${item.order}: ${item.source.source_type} ${item.timeline_start_seconds}–${item.timeline_end_seconds}s`} style={{ left: `${item.timeline_start_seconds / duration * 100}%`, width: `${(item.timeline_end_seconds - item.timeline_start_seconds) / duration * 100}%` }}>{item.order + 1}</div>)}
      {timeline.gaps.map((gap) => <div className="timeline-gap" key={gap.id} title={`Gap ${gap.timeline_start_seconds}–${gap.timeline_end_seconds}s`} style={{ left: `${gap.timeline_start_seconds / duration * 100}%`, width: `${(gap.timeline_end_seconds - gap.timeline_start_seconds) / duration * 100}%` }}>gap</div>)}
    </div>
    <h3>Placements</h3>
    <TableWrap><table><thead><tr><th>#</th><th>Timeline</th><th>Fonte</th><th>Source range / apresentação</th><th>Estratégia</th><th>Queries</th></tr></thead><tbody>{timeline.tracks.primary_visual.placements.map((item) => {
      const source = item.source;
      const provenance = source.provenance as Record<string, unknown> | undefined;
      return <tr key={item.id}><td>{item.order + 1}</td><td>{item.timeline_start_seconds.toFixed(2)}–{item.timeline_end_seconds.toFixed(2)}s</td><td><StatusBadge value={source.source_type} /><small className="block-meta">{String(provenance?.media_filename || provenance?.filename || source.media_id || "—")}</small></td><td>{source.source_type === "static_image" ? <Diagnostics label="Static presentation" value={item.presentation} /> : `${Number(source.source_start_seconds).toFixed(2)}–${Number(source.source_end_seconds).toFixed(2)}s`}<small className="block-meta">transição {item.transition}</small></td><td>{item.strategy}<small className="block-meta">{item.importance}</small></td><td>{item.visual_query_ids.map((id) => <code className="id-chip" key={id}>{id.slice(0, 8)}</code>)}</td></tr>;
    })}</tbody></table></TableWrap>
    <h3>Gaps explícitos</h3>
    {timeline.gaps.length === 0 ? <EmptyState title="Timeline sem gaps" /> : <TableWrap><table><thead><tr><th>Range</th><th>Duração</th><th>Classificação</th><th>Motivo</th><th>Queries</th></tr></thead><tbody>{timeline.gaps.map((gap) => <tr key={gap.id} className="gap-row"><td>{gap.timeline_start_seconds.toFixed(2)}–{gap.timeline_end_seconds.toFixed(2)}s</td><td>{(gap.timeline_end_seconds - gap.timeline_start_seconds).toFixed(2)}s</td><td><StatusBadge value={gap.severity} /></td><td>{gap.reason}</td><td>{gap.related_visual_query_ids.map((id) => <code className="id-chip" key={id}>{id.slice(0, 8)}</code>)}</td></tr>)}</tbody></table></TableWrap>}
    <Diagnostics value={timeline.query_placement_outcomes} label="Query placement outcomes" />
    <Diagnostics value={timeline.diagnostics} />
  </Card>;
}

export default function VisualsPage() {
  const { snapshot } = useWorkflowContext();
  const id = snapshot.workflow.id;
  const planLoader = useCallback((signal?: AbortSignal) => operatorApi.getVisualPlan(id, signal), [id]);
  const timelineLoader = useCallback((signal?: AbortSignal) => operatorApi.getTimeline(id, signal), [id]);
  const plan = useColdArtifact<VisualPlanDetail>(id, "visual-plan", snapshot.artifacts.visual_plan?.fingerprint, planLoader);
  const timeline = useColdArtifact<ProductionTimelineDetail>(id, "timeline", snapshot.artifacts.production_timeline?.fingerprint, timelineLoader);
  if (plan.loading || timeline.loading) return <LoadingState label="Carregando decisões visuais…" />;
  return <div className="workspace-stack">
    {(plan.error || timeline.error) && <Alert tone="danger">{plan.error || timeline.error}</Alert>}
    {!plan.data ? <EmptyState title="VisualPlan ainda não disponível" detail="A ausência esperada não dispara Media Search." /> : <Card title="VisualPlan">
      <DefinitionList items={[["Status", <StatusBadge value={plan.data.status} />], ["Queries resolvidas", `${plan.data.resolved_queries}/${plan.data.total_queries}`], ["Gaps required / optional", `${plan.data.required_gaps} / ${plan.data.optional_gaps}`], ["Estratégia", plan.data.strategy_version], ["Fingerprint", <code>{plan.data.fingerprint}</code>]]} />
      <div className="decision-list">{plan.data.decisions.map((decision) => <details className="decision-card" key={decision.id} open={decision.importance === "required" && decision.source_resolution_status !== "resolved"}>
        <summary><span><strong>Q{decision.query_order + 1}: {decision.query}</strong><small>{decision.intent} · {decision.importance} · narração {decision.narration_start_seconds?.toFixed(2) ?? "—"}–{decision.narration_end_seconds?.toFixed(2) ?? "—"}s</small></span><StatusBadge value={decision.source_resolution_status} /></summary>
        <DefinitionList items={[["Estratégia", decision.selection_strategy || "—"], ["Score", decision.selection_score?.toFixed(3) || "—"], ["Binding", decision.narration_binding_status], ["Motivo", decision.reason || "—"]]} />
        <h4>Fonte selecionada</h4>{decision.selected_source ? <Source source={decision.selected_source} selected /> : <EmptyState title="Nenhuma fonte selecionada" detail={decision.reason || undefined} />}
        <details className="source-alternatives"><summary>Alternativas ({decision.alternatives.length})</summary>{decision.alternatives.map((source, index) => <Source key={`${source.media_filename}-${index}`} source={source} />)}</details>
        <Diagnostics label="Componentes do score" value={decision.score_components} /><Diagnostics value={decision.diagnostics} />
      </details>)}</div>
      <Diagnostics value={plan.data.diagnostics} />
    </Card>}
    {timeline.data ? <Timeline timeline={timeline.data} /> : <EmptyState title="Production Timeline ainda não disponível" />}
  </div>;
}
