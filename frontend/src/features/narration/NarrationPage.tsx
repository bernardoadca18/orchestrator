import { useCallback, useEffect, useState } from "react";
import { api, AudioBloco, Workflow } from "../../api/client";
import { BlockChunks } from "../../components/audio-edit/BlockChunks";
import { PendingAudioEdits } from "../../components/audio-edit/PendingAudioEdits";
import { useAudioEditUpdate } from "../../components/audio-edit/useAudioEditUpdate";
import { Alert, Card, ConfirmDialog, Diagnostics, EmptyState, LoadingState, StatusBadge, TableWrap } from "../../components/ui";
import { createActionKey } from "../../data/operatorAction";
import { startSerialPolling } from "../../utils/serialPolling";
import { presentContentQuality } from "../../utils/contentQuality";
import { useWorkflowContext } from "../workflow/WorkflowWorkspace";

function TakeDiagnostics({ take }: { take: AudioBloco["takes"][number] }) {
  const quality = presentContentQuality(take.content_quality);
  return <details>
    <summary>Proveniência e qualidade</summary>
    <dl className="definition-list compact">
      <div><dt>Seed</dt><dd>{take.resolved_seed ?? take.requested_seed ?? "—"}</dd></div>
      <div><dt>Voz</dt><dd>{take.voice_profile || "—"} / {take.voice_profile_identity || "—"}</dd></div>
      <div><dt>Geração</dt><dd>{take.generation_profile || "—"} / {take.generation_profile_identity || "—"}</dd></div>
      <div><dt>Modelo</dt><dd>{take.model_id || "—"} / {take.model_revision || "—"}</dd></div>
      <div><dt>SHA</dt><dd><code>{take.audio_sha256 || "—"}</code></dd></div>
      <div><dt>Qualidade</dt><dd>{quality ? `${quality.outcomeLabel}: ${quality.classificationLabel}` : take.quality_status || "—"}</dd></div>
    </dl>
    {take.synthesis_text && <p><strong>Texto de síntese:</strong> {take.synthesis_text}</p>}
    <Diagnostics label="Diagnóstico de conteúdo" value={take.content_quality} />
    <Diagnostics label="Diagnóstico de accent" value={take.accent_quality} />
    <Diagnostics label="Proveniência do provider" value={take.provider_metadata} />
  </details>;
}

export default function NarrationPage() {
  const { snapshot, refresh: refreshSnapshot } = useWorkflowContext();
  const workflowId = snapshot.workflow.id;
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<{ kind: "block"; order: number } | { kind: "narration" } | { kind: "rebuild" } | null>(null);
  const [busy, setBusy] = useState(false);
  const [chunkRefreshRevision, setChunkRefreshRevision] = useState(0);

  const load = useCallback(async (signal?: AbortSignal) => {
    const next = await api.obterWorkflow(workflowId, signal);
    setWorkflow(next);
    setError(null);
  }, [workflowId]);

  useEffect(() => startSerialPolling(load, {
    intervalMs: snapshot.workflow.production_phase === "audio" && ["queued", "running"].includes(snapshot.workflow.production_status || "") ? 4_000 : 20_000,
    maxBackoffMs: 60_000,
    onError: (reason) => setError(reason instanceof Error ? reason.message : "Falha ao atualizar narração"),
  }), [load, snapshot.workflow.production_phase, snapshot.workflow.production_status]);

  const refreshAll = useCallback(async () => { await load(); refreshSnapshot(); }, [load, refreshSnapshot]);
  const audioEdit = useAudioEditUpdate({
    workflowId,
    editRevision: workflow?.audio_edit_revision ?? 0,
    onRefresh: refreshAll,
    onPublished: () => setChunkRefreshRevision((value) => value + 1),
  });

  async function retryBlock(order: number) {
    setBusy(true);
    try { await api.retryAudioBlock(workflowId, order, createActionKey("retry-block", workflowId)); await refreshAll(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Falha no retry"); }
    finally { setBusy(false); }
  }

  async function regenerate() {
    setBusy(true);
    try {
      if (confirm?.kind === "block") await api.regenerateAudioBlock(workflowId, confirm.order, createActionKey("regenerate-block", workflowId));
      if (confirm?.kind === "narration") await api.regenerateNarration(workflowId, createActionKey("regenerate-narration", workflowId));
      if (confirm?.kind === "rebuild") await api.rebuildNarrationFromTakes(workflowId, createActionKey("rebuild-narration", workflowId));
      setConfirm(null);
      await refreshAll();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Falha ao regenerar"); }
    finally { setBusy(false); }
  }

  if (!workflow) return <LoadingState label="Carregando narração…" />;
  return <div className="workspace-stack">
    {error && <Alert tone="danger">{error}</Alert>}
    <Card title="Narração final">
      {workflow.audio_available ? <audio className="media-player" key={workflow.audio_final_sha256 || "audio"} controls preload="metadata" src={api.urlAudio(workflowId, workflow.audio_final_sha256)} /> : <EmptyState title="Narração final ainda não disponível" />}
      <dl className="definition-list compact">
        <div><dt>SHA</dt><dd><code>{workflow.audio_final_sha256 || "—"}</code></dd></div>
        <div><dt>Duração</dt><dd>{workflow.duracao_audio_segundos?.toFixed(2) || "—"}s</dd></div>
        <div><dt>Formato</dt><dd>{workflow.audio_final_sample_rate || "—"} Hz · {workflow.audio_final_channels || "—"} canal(is) · {workflow.audio_final_subtype || "—"}</dd></div>
        <div><dt>Mastering</dt><dd>{String(workflow.mastering_config?.profile_id || workflow.mastering_config?.policy_version || "configurado no backend")}</dd></div>
      </dl>
      <Diagnostics label="Mastering metrics" value={workflow.mastering_metrics} />
      <button disabled={busy || snapshot.workflow.capabilities.can_regenerate_narration?.allowed === false} onClick={() => setConfirm({ kind: "narration" })}>Regenerar narração completa</button>
      {snapshot.workflow.capabilities.can_regenerate_narration?.reason && <small className="block-meta">{snapshot.workflow.capabilities.can_regenerate_narration.reason}</small>}
      <button disabled={busy || snapshot.workflow.capabilities.can_rebuild_narration_from_takes?.allowed === false} onClick={() => setConfirm({ kind: "rebuild" })}>Reprocessar narração</button>
      {snapshot.workflow.capabilities.can_rebuild_narration_from_takes?.reason && <small className="block-meta">{snapshot.workflow.capabilities.can_rebuild_narration_from_takes.reason}</small>}
    </Card>
    <PendingAudioEdits workflow={workflow} update={audioEdit.update} submitting={audioEdit.submitting} error={audioEdit.error} notice={audioEdit.notice} onApply={() => void audioEdit.apply(workflow.audio_edit_revision)} />
    <Card title={`Blocos e takes (${workflow.blocos?.length || 0})`}>
      {!workflow.blocos?.length ? <EmptyState title="Nenhum bloco narrativo" /> : <TableWrap><table>
        <thead><tr><th># / tipo</th><th>Status</th><th>Texto</th><th>Take corrente e histórico</th><th>Áudio</th><th>Chunks</th></tr></thead>
        <tbody>{workflow.blocos.map((block) => <tr key={block.id}>
          <td><strong>{block.ordem + 1}</strong><small className="block-meta">{block.tipo}</small></td>
          <td><StatusBadge value={block.status} />{block.erro_mensagem && <span className="block-meta attention-text">{block.erro_mensagem}</span>}<div className="inline-actions">{block.status === "erro" && <button disabled={busy} onClick={() => void retryBlock(block.ordem)}>Retry</button>}<button disabled={busy} onClick={() => setConfirm({ kind: "block", order: block.ordem })}>Novo take</button></div></td>
          <td><details><summary>Ver textos</summary><p><strong>Canônico:</strong> {block.texto}</p><p><strong>Normalizado:</strong> {block.normalized_text || "—"}</p><p><strong>Síntese:</strong> {block.synthesis_text || "—"}</p><Diagnostics label="Pronúncias" value={block.pronunciation_replacements} /></details></td>
          <td><strong>Take {block.generation_attempt + 1}</strong><small className="block-meta">seed {block.resolved_seed ?? "—"} · {block.takes.length} históricos</small><details><summary>Histórico completo</summary><ol>{block.takes.map((take) => <li key={take.id}><StatusBadge value={take.status} /> #{take.attempt_index} {take.kind}{take.is_current ? " · corrente" : ""}{take.error_message && <span className="attention-text"> {take.error_message}</span>}<TakeDiagnostics take={take} /></li>)}</ol></details></td>
          <td>{block.audio_sha256 ? <audio controls preload="metadata" src={api.urlAudioBloco(workflowId, block.id, block.audio_sha256)} /> : "—"}</td>
          <td><BlockChunks workflowId={workflowId} blocoId={block.id} blocoStatus={block.status} dirty={block.audio_edit_dirty} refreshRevision={chunkRefreshRevision} onWorkflowRefresh={refreshAll} /></td>
        </tr>)}</tbody>
      </table></TableWrap>}
    </Card>
    <ConfirmDialog open={confirm !== null} title={confirm?.kind === "narration" ? "Regenerar narração" : confirm?.kind === "rebuild" ? "Reprocessar narração" : "Gerar novo take do bloco"} consequence={confirm?.kind === "narration" ? "Preserva takes históricos e invalida somente os artefatos downstream dependentes da nova narração." : confirm?.kind === "rebuild" ? "Reprocessa a narração usando os takes existentes com o tempo atual. Não gera novos takes." : "O take atual permanece histórico; o backend decide quando o candidato pode ser promovido."} invalidates={confirm?.kind === "narration" ? snapshot.workflow.capabilities.can_regenerate_narration?.invalidates : confirm?.kind === "rebuild" ? snapshot.workflow.capabilities.can_rebuild_narration_from_takes?.invalidates : []} confirmLabel={confirm?.kind === "rebuild" ? "Reprocessar" : "Confirmar regeneração"} dangerous onClose={() => setConfirm(null)} onConfirm={() => void regenerate()} />
  </div>;
}
