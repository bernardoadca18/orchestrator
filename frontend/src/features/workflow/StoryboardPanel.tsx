import { useEffect, useState } from "react";
import { api, BASE_URL, request } from "../../api/client";
import { Card, Alert } from "../../components/ui";

type Scene = {
  id: string; order: number; start_seconds: number; end_seconds: number;
  narration_anchor: { text: string }; visual_intent: string; prompt: string;
  motion_intent: string; preview_url: string | null; asset: { expected_sha256: string } | null;
};
type Board = { status: string; ready: boolean; scenes: Scene[] };
const time = (s: number) => `${Math.floor(s / 60).toString().padStart(2, '0')}:${(s % 60).toFixed(3).padStart(6, '0')}`;

export function StoryboardPanel({ workflowId, phase, onChange }: { workflowId: string; phase: string | null; onChange: () => void }) {
  const [board, setBoard] = useState<Board | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
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
  async function resume() {
    setBusy(true); setError(null);
    try { await api.retomarProducao(workflowId); onChange(); }
    catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  return <Card title="Storyboard" className="span-full">
    {error && <Alert tone="danger">{error}</Alert>}
    {!board?.scenes.length && <p>{board?.status === 'stale' ? 'Timing desatualizado. Retome a produção para resolver contra o áudio atual.' : 'As cenas aparecerão após o alinhamento da narração.'}</p>}
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 20 }}>
      {board?.scenes.map(scene => <article key={scene.id} style={{ border: '1px solid #4b5563', borderRadius: 8, padding: 16 }}>
        <h3>Cena {String(scene.order+1).padStart(2,'0')} · {scene.id}</h3>
        <p>{time(scene.start_seconds)} → {time(scene.end_seconds)}</p>
        <blockquote>{scene.narration_anchor.text}</blockquote>
        <p><strong>Intenção visual:</strong> {scene.visual_intent}</p>
        <details><summary>Prompt</summary><p>{scene.prompt}</p></details>
        <p>Movimento: {scene.motion_intent}</p>
        {scene.preview_url && <img src={`${BASE_URL}${scene.preview_url}?sha=${scene.asset?.expected_sha256}`} alt={scene.visual_intent} style={{ width: '100%', maxHeight: 230, objectFit: 'contain' }} />}
        <label>{scene.asset ? 'Substituir imagem' : 'Enviar imagem'}<input type="file" accept="image/png,image/jpeg,image/webp" disabled={busy} onChange={event => { const file = event.target.files?.[0]; if(file) void change(scene,file); event.target.value = ''; }} /></label>
        {scene.asset && <button disabled={busy} onClick={() => void change(scene)}>Remover imagem</button>}
      </article>)}
    </div>
    {phase === 'storyboard_assets' && <button disabled={busy || !board?.ready} onClick={() => void resume()}>Continuar produção</button>}
    {board?.scenes.length && !board.ready ? <p>Envie uma imagem para cada cena para continuar.</p> : null}
  </Card>;
}
