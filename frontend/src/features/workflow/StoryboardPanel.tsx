import { useEffect, useState } from "react";
import { api, BASE_URL, request } from "../../api/client";
import { Card, Alert } from "../../components/ui";

type Scene = {
  id: string; order: number; start_seconds: number; end_seconds: number;
  narration_anchor: { text: string }; visual_intent: string; prompt: string;
  motion_intent: string; preview_url: string | null; asset: { expected_sha256: string; width?: number; height?: number; expected_byte_size?: number; content_type?: string } | null;
  quality_warnings?: string[]; presentation?: { fit: "cover" | "contain" };
};
type Board = { status: string; ready: boolean; scenes: Scene[]; render_profile?: { width: number; height: number; name: string } | null; quality?: { scene_count: number; average_scene_duration: number; min_scene_duration: number; max_scene_duration: number; caption_cue_count: number; audio_mastering_status: string; timeline_coverage: number; issues: Array<{ code: string; severity: string; scene_id: string }> } };
const time = (s: number) => `${Math.floor(s / 60).toString().padStart(2, '0')}:${(s % 60).toFixed(3).padStart(6, '0')}`;

export function StoryboardPanel({ workflowId, phase, onChange }: { workflowId: string; phase: string | null; onChange: () => void }) {
  const [board, setBoard] = useState<Board | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [batch, setBatch] = useState<File[]>([]);
  useEffect(() => {
    let active = true;
    request<Board>(`/workflow/${workflowId}/storyboard`).then(value => { if (active) setBoard(value); }).catch(e => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, [workflowId, phase]);
  async function change(scene: Scene, file?: File) {
    setBusy(true); setError(null);
    try {
      const url = `/workflow/${workflowId}/storyboard/scenes/${scene.id}/image`;
      if (file) {
        const data = new FormData(); data.append('image', file);
        const response = await fetch(`${BASE_URL}${url}`, { method: 'POST', body: data });
        if (!response.ok) throw new Error(JSON.stringify((await response.json()).detail));
        setBoard(await response.json());
      } else setBoard(await request<Board>(url, { method: 'DELETE' }));
      onChange();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  const mapped = batch.map(file => ({ file, scene: board?.scenes.find(s => s.id === file.name.replace(/\.[^.]+$/, '')) }));
  const batchValid = mapped.length > 0 && mapped.every(m => m.scene) && new Set(mapped.map(m => m.scene?.id)).size === mapped.length;
  async function uploadBatch() {
    setBusy(true); setError(null);
    try {
      const form = new FormData();
      for (const item of mapped) {
        form.append('scene_ids', item.scene!.id);
        form.append('images', item.file);
      }
      const response = await fetch(`${BASE_URL}/workflow/${workflowId}/storyboard/images/batch`, { method: 'POST', body: form });
      if (!response.ok) throw new Error(JSON.stringify((await response.json()).detail));
      const result = await response.json() as { board: Board; results: Array<{ scene_id: string; status: string }> };
      setBoard(result.board);
      setBatch([]); onChange();
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  async function resume() {
    setBusy(true); setError(null);
    try { await api.retomarProducao(workflowId); onChange(); }
    catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  return <Card title="Storyboard" className="span-full">
    {error && <Alert tone="danger">{error}</Alert>}
    {board?.quality && <details open><summary>Qualidade antes do render</summary>
      <p>{board.quality.scene_count} cenas · média {board.quality.average_scene_duration.toFixed(1)}s · mínima {board.quality.min_scene_duration.toFixed(1)}s · máxima {board.quality.max_scene_duration.toFixed(1)}s</p>
      <p>Cobertura: {(board.quality.timeline_coverage * 100).toFixed(1)}% · {board.quality.caption_cue_count} legendas · mastering: {board.quality.audio_mastering_status}</p>
      <ul>{board.quality.issues.map((i, n) => <li key={n}>{i.severity}: {i.scene_id} — {i.code}</li>)}</ul>
    </details>}
    {board?.scenes.length ? <details><summary>Enviar várias imagens</summary>
      <p>Use o ID da cena como nome: scene-001.png, scene-002.png. Confira as associações antes de enviar. Para substituir uma imagem existente, use o envio da cena.</p>
      <input type="file" multiple accept="image/png,image/jpeg,image/webp" disabled={busy} onChange={e => setBatch(Array.from(e.target.files || []))} />
      <ul>{mapped.map((m, i) => <li key={i}>{m.file.name} → {m.scene?.id || 'sem correspondência'}</li>)}</ul>
      <button disabled={busy || !batchValid || mapped.length > 20} onClick={() => void uploadBatch()}>{busy ? 'Enviando imagens…' : `Confirmar envio de ${mapped.length} imagens`}</button>
      {mapped.length > 20 && <p>Envie até 20 imagens por lote.</p>}
    </details> : null}
    {!board?.scenes.length && <p>{board?.status === 'stale' ? 'Timing desatualizado. Retome a produção para resolver contra o áudio atual.' : 'As cenas aparecerão após o alinhamento da narração.'}</p>}
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 20 }}>
      {board?.scenes.map(scene => <article key={scene.id} style={{ border: '1px solid #4b5563', borderRadius: 8, padding: 16 }}>
        <h3>Cena {String(scene.order+1).padStart(2,'0')} · {scene.id}</h3>
        <p>{time(scene.start_seconds)} → {time(scene.end_seconds)}</p>
        <blockquote>{scene.narration_anchor.text}</blockquote>
        <p><strong>Intenção visual:</strong> {scene.visual_intent}</p>
        <details><summary>Prompt</summary><p>{scene.prompt}</p></details>
        <p>Movimento: {scene.motion_intent}</p>
        {scene.asset && <p>{scene.asset.width || '?'} × {scene.asset.height || '?'} · {scene.asset.width && scene.asset.height ? (scene.asset.width / scene.asset.height).toFixed(2) + ':1' : 'dimensões indisponíveis'} · {((scene.asset.expected_byte_size || 0) / 1024 / 1024).toFixed(2)} MB · {scene.asset.content_type}</p>}
        {scene.quality_warnings?.map(w => <p key={w}>{w}</p>)}
        {scene.preview_url && <img src={`${BASE_URL}${scene.preview_url}?sha=${scene.asset?.expected_sha256}`} alt={scene.visual_intent} style={{ width: '100%', aspectRatio: board?.render_profile ? `${board.render_profile.width} / ${board.render_profile.height}` : undefined, objectFit: board?.render_profile ? (scene.presentation?.fit || 'cover') : 'contain' }} />}
        <label>{scene.asset ? 'Substituir imagem' : 'Enviar imagem'}<input type="file" accept="image/png,image/jpeg,image/webp" disabled={busy} onChange={event => { const file = event.target.files?.[0]; if(file) void change(scene,file); event.target.value = ''; }} /></label>
        {scene.asset && <button disabled={busy} onClick={() => void change(scene)}>Remover imagem</button>}
      </article>)}
    </div>
    {phase === 'storyboard_assets' && <button disabled={busy || !board?.ready} onClick={() => void resume()}>Continuar produção</button>}
    {board?.scenes.length && !board.ready ? <p>Envie uma imagem para cada cena para continuar.</p> : null}
  </Card>;
}
