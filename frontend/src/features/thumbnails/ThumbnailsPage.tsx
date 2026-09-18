import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { BASE_URL, request } from "../../api/client";
import { Alert, LoadingState, StatusBadge } from "../../components/ui";

type Candidate = {
  candidate_id: string; template_id: string; headline: string;
  status: "available" | "unavailable"; error_category: string | null;
  artifact: { sha256: string } | null;
  sources: { media_id: string; filename: string; source_type: string; game?: string; session_label?: string }[];
};
type Package = {
  id: string; creative_revision: number; status: string; selected_candidate_id: string | null;
  result: { candidates: Candidate[] } | null; error_message: string | null;
  requires_regeneration: boolean;
};
type ThumbnailState = { current_package: Package | null; history: { id: string; creative_revision: number; status: string }[] };

export default function ThumbnailsPage() {
  const { workflowId = "" } = useParams();
  const [state, setState] = useState<ThumbnailState | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState(0);
  const path = `/operator/workflows/${workflowId}/thumbnails`;
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setState(null);
    async function load() {
      try {
        const next = await request<ThumbnailState>(path, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setState(next);
        setError("");
        if (["pending", "running"].includes(next.current_package?.status || "")) timer = setTimeout(load, 2500);
      } catch (err) {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : "Falha ao carregar thumbnails");
          timer = setTimeout(load, 5000);
        }
      }
    }
    void load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [path, version]);
  async function action(name: string, body?: object) {
    setBusy(true); setError("");
    try {
      await request(`${path}/${name}`, { method: "POST", ...(body ? { body: JSON.stringify(body) } : {}) });
      setVersion(v => v + 1);
    } catch (err) { setError(err instanceof Error ? err.message : "Falha na operação"); }
    finally { setBusy(false); }
  }
  const current = state?.current_package;
  const running = ["pending", "running"].includes(current?.status || "");
  return <section className="thumbnail-page">
    <div className="thumbnail-toolbar"><div><p className="eyebrow">Packaging · revisão humana</p>
      <h2>Thumbnails</h2><p>Compare as opções e selecione a imagem para esta produção.</p></div>
      {state && <button disabled={busy || running} onClick={() => void action(current?.status === "completed" ? "regenerate" : "generate")}>
        {running ? "Gerando opções…" : current?.status === "completed" ? "Gerar novas opções" : current?.status === "failed" ? "Tentar novamente" : "Gerar thumbnails"}
      </button>}
    </div>
    {error && <Alert tone="danger">{error}</Alert>}
    {!state && !error && <LoadingState label="Carregando thumbnails…" />}
    {current && <p className="status-row"><StatusBadge value={current.status} /> Revisão criativa {current.creative_revision}
      {current.selected_candidate_id && <strong>Selecionada: opção {current.selected_candidate_id}</strong>}</p>}
    {current?.requires_regeneration && <Alert tone="warning">Gere novas opções para corrigir a correspondência entre texto e imagem.</Alert>}
    {current?.error_message && <Alert tone="danger">{current.error_message}</Alert>}
    {running && <LoadingState label="Dirigindo conceitos, buscando assets e compondo três opções…" />}
    <div className="thumbnail-grid">{current?.result?.candidates.map(candidate => {
      const selected = current.selected_candidate_id === candidate.candidate_id;
      return <article key={candidate.candidate_id} className={`thumbnail-card ${selected ? "is-selected" : ""}`}>
        {candidate.artifact ? <img alt={`Opção ${candidate.candidate_id}: ${candidate.headline || "sem texto"}`}
          src={`${BASE_URL}${path}/${candidate.candidate_id}/content?package_id=${encodeURIComponent(current.id)}`} /> : <div className="thumbnail-unavailable">Sem asset adequado para esta opção</div>}
        <div className="thumbnail-card-body"><h3>Opção {candidate.candidate_id} {selected && "· Selecionada"}</h3>
          <p>{candidate.headline || "Sem headline"}</p><small>{candidate.template_id}</small>
          <ul>{candidate.sources.map((source, i) => <li key={`${source.media_id}-${i}`}>{source.filename} · {source.source_type === "image" ? "Imagem" : "Frame de vídeo"}
            {source.game && ` · ${source.game}`}{source.session_label && ` · ${source.session_label}`}</li>)}</ul>
          {candidate.error_category && <p role="status">Opção indisponível ({candidate.error_category})</p>}
          <button disabled={busy || current.requires_regeneration || candidate.status !== "available" || selected} aria-pressed={selected}
            onClick={() => void action("select", { candidate_id: candidate.candidate_id, package_id: current.id })}>{selected ? "Selecionada" : "Selecionar"}</button>
        </div></article>;
    })}</div>
    {(state?.history.length || 0) > 1 && <details><summary>Gerações anteriores preservadas</summary>
      <ul>{state?.history.filter(p => p.id !== current?.id).map(p => <li key={p.id}>Revisão {p.creative_revision} · {p.status}</li>)}</ul></details>}
  </section>;
}
