import { useCallback, useEffect, useMemo, useState } from "react";
import { AudioMixPlan, ComposerInput, MusicAsset, operatorApi } from "../../api/operator";
import { ActionButton, Alert, Card, ConfirmDialog, DefinitionList, Diagnostics, EmptyState, LoadingState, StatusBadge, TableWrap } from "../../components/ui";
import { coldArtifactCache } from "../../data/coldArtifactCache";
import { createActionKey } from "../../data/operatorAction";
import { useColdArtifact } from "../../hooks/useColdArtifact";
import { useOperatorAction } from "../../hooks/useOperatorAction";
import { useWorkflowContext } from "../workflow/WorkflowWorkspace";

function Plan({ plan }: { plan: AudioMixPlan }) {
  const music = plan.music;
  return <div className="workspace-grid">
    <Card title="AudioMixPlan"><DefinitionList items={[["Schema", plan.schema_version], ["Estratégia / profile", `${plan.strategy_version} / ${plan.profile_id}`], ["Fingerprint", <code>{plan.fingerprint}</code>], ["Duração", `${plan.duration_seconds.toFixed(2)}s`]]} /></Card>
    <Card title="Narração"><DefinitionList items={[["SHA", <code>{plan.narration.sha256}</code>], ["Duração", `${plan.narration.duration_seconds.toFixed(2)}s`], ["Formato", `${plan.narration.sample_rate} Hz · ${plan.narration.channels} canal`]]} /></Card>
    <Card title="Música">{!music.enabled ? <EmptyState title="Música desabilitada pelo roteiro" /> : <><DefinitionList items={[["Asset", music.asset.asset_id], ["SHA", <code>{music.asset.sha256}</code>], ["Duração / formato", `${music.asset.duration_seconds.toFixed(2)}s · ${music.asset.format}`], ["Mood / style", `${music.asset.moods.join(", ")} / ${music.asset.styles.join(", ")}`], ["Energy / loop", `${music.asset.energy.toFixed(2)} / ${music.asset.loopable ? "sim" : "não"}`], ["Owner / licença", `${music.asset.source_owner} / ${music.asset.license_type}`], ["Monetização", <StatusBadge value={music.asset.monetization_allowed ? "allowed" : "blocked"} />], ["Attribution", music.asset.attribution_required ? music.asset.attribution_text || "obrigatória" : "não obrigatória"]]} /><audio className="media-player" controls preload="metadata" src={operatorApi.musicPreviewUrl(music.asset)} /></>}</Card>
    <Card title="Mix e segmentos"><DefinitionList items={music.enabled ? [["Normalização", `${music.normalization_policy} · ${music.normalization_target_lufs} LUFS`], ["Loop", music.loop_policy], ["Segmentos", String(music.segments.length)]] : [["Segmentos", "0"]]} />{music.enabled && <TableWrap><table><thead><tr><th>Range</th><th>Gain</th><th>Fades</th><th>Transição</th></tr></thead><tbody>{music.segments.map((segment, index) => <tr key={index}><td>{Number(segment.start_seconds).toFixed(2)}–{Number(segment.end_seconds).toFixed(2)}s</td><td>{segment.gain_adjustment_db} dB</td><td>{segment.fade_in_seconds}s / {segment.fade_out_seconds}s</td><td>{segment.transition_preset}</td></tr>)}</tbody></table></TableWrap>}</Card>
    <Card title="Ducking"><DefinitionList items={[["Policy", plan.ducking.policy_version], ["Base / ducked", `${plan.ducking.base_gain_db} / ${plan.ducking.ducked_gain_db} dB`], ["Attack / release", `${plan.ducking.attack_seconds}s / ${plan.ducking.release_seconds}s`], ["Regiões de fala", String(plan.ducking.speech_regions.length)]]} /><details><summary>Regiões de fala</summary><ol>{plan.ducking.speech_regions.map((region, index) => <li key={index}>{region.start_seconds.toFixed(2)}–{region.end_seconds.toFixed(2)}s</li>)}</ol></details></Card>
    <Card title="Program master / output"><Diagnostics label="Program master" value={plan.program_master} /><Diagnostics label="Output" value={plan.output} /></Card>
  </div>;
}

export default function ProgramAudioPage() {
  const { snapshot, refresh } = useWorkflowContext();
  const workflow = snapshot.workflow;
  const identity = [snapshot.artifacts.production_timeline?.fingerprint, snapshot.artifacts.presentation_fingerprint, snapshot.artifacts.audio_mix_plan_fingerprint].filter(Boolean).join(":");
  const loader = useCallback((signal?: AbortSignal) => operatorApi.getComposerInput(workflow.id, signal), [workflow.id]);
  const result = useColdArtifact<ComposerInput>(workflow.id, "composer-input", identity, loader);
  const [catalog, setCatalog] = useState<MusicAsset[]>([]);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [selection, setSelection] = useState<string>(workflow.music_override_asset_id || "");
  const [confirm, setConfirm] = useState<"set" | "clear" | null>(null);
  const action = useOperatorAction(refresh);
  useEffect(() => { const controller = new AbortController(); operatorApi.getMusicCatalog(controller.signal).then((data) => setCatalog(data.assets)).catch((reason) => { if (!controller.signal.aborted) setCatalogError(reason instanceof Error ? reason.message : "Catálogo indisponível"); }); return () => controller.abort(); }, []);
  useEffect(() => setSelection(workflow.music_override_asset_id || ""), [workflow.music_override_asset_id]);
  const selected = useMemo(() => catalog.find((item) => item.asset_id === selection) || null, [catalog, selection]);

  async function mutate() {
    const operation = confirm === "clear"
      ? () => operatorApi.clearMusicOverride(workflow.id, workflow.music_override_revision, createActionKey("music-clear", workflow.id))
      : () => operatorApi.setMusicOverride(workflow.id, selection, workflow.music_override_revision, createActionKey("music-set", workflow.id));
    const response = await action.run(operation, confirm === "clear" ? "Seleção automática restaurada; novo render enfileirado." : "Override salvo; novo render enfileirado.");
    if (response) coldArtifactCache.invalidateWorkflow(workflow.id, ["composer-input"]);
    setConfirm(null);
  }

  if (result.loading) return <LoadingState label="Carregando AudioMixPlan…" />;
  return <div className="workspace-stack">
    {result.error && <Alert tone="danger">{result.error}</Alert>}
    {result.data?.audio_mix_plan ? <Plan plan={result.data.audio_mix_plan} /> : <EmptyState title="AudioMixPlan ainda não disponível" detail="O workflow pode usar o contrato v1 sem Program Audio, ou o artefato ainda não foi produzido." />}
    <Card title="Seleção operacional de música">
      {catalogError && <Alert tone="danger">{catalogError}</Alert>}
      <p>Override atual: <strong>{workflow.music_override_asset_id || "seleção automática"}</strong> · revisão {workflow.music_override_revision}</p>
      <div className="inline-form"><label>Faixa do catálogo<select value={selection} onChange={(event) => setSelection(event.target.value)}><option value="">Selecione…</option>{catalog.map((asset) => <option key={asset.asset_id} value={asset.asset_id}>{asset.asset_id} · {asset.moods.join(", ")}</option>)}</select></label>
      <ActionButton capability={workflow.capabilities.can_change_music} disabled={!selected || action.submitting} onClick={() => setConfirm("set")}>Usar faixa selecionada</ActionButton>
      <ActionButton capability={workflow.capabilities.can_change_music} disabled={workflow.music_override_revision > 0 && workflow.music_override_asset_id === null || action.submitting} onClick={() => setConfirm("clear")}>Restaurar seleção automática</ActionButton></div>
      {selected && <><DefinitionList items={[["Direitos", `${selected.license_type} · monetização ${selected.monetization_allowed ? "permitida" : "bloqueada"}`], ["Owner", selected.source_owner], ["Attribution", selected.attribution_required ? selected.attribution_text || "obrigatória" : "não obrigatória"]]} /><audio className="media-player" controls preload="metadata" src={operatorApi.musicPreviewUrl(selected)} /></>}
      {action.error && <Alert tone={action.conflict ? "warning" : "danger"}>{action.error}</Alert>}{action.notice && <Alert tone="success">{action.notice}</Alert>}
    </Card>
    <ConfirmDialog open={confirm !== null} title={confirm === "clear" ? "Restaurar seleção automática" : `Usar ${selected?.asset_id || "faixa"}`} consequence="A mudança altera o AudioMixPlan. O render, QA e Human Review correntes ficam históricos; narração, alignment, VisualPlan e ProductionTimeline são preservados." invalidates={workflow.capabilities.can_change_music?.invalidates} confirmLabel={confirm === "clear" ? "Restaurar e renderizar" : "Salvar e renderizar"} dangerous onClose={() => setConfirm(null)} onConfirm={() => void mutate()} />
  </div>;
}
