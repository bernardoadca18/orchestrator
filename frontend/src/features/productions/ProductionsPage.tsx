import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { OperatorWorkflowSummary, operatorApi } from "../../api/operator";
import { workflowPath } from "../../app/routes";
import { Alert, EmptyState, LoadingState, PageHeader, StatusBadge, TableWrap } from "../../components/ui";
import { startSerialPolling } from "../../utils/serialPolling";

export const productionStates = ["", "active", "needs_attention", "needs_review", "completed", "failed", "cancelled"];

export function productionViewState(itemCount: number, loading: boolean, error: string | null) {
  if (loading && itemCount === 0) return "loading";
  if (error && itemCount === 0) return "error";
  if (itemCount === 0) return "empty";
  return "ready";
}

export default function ProductionsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const state = searchParams.get("state") || "";
  const search = searchParams.get("q") || "";
  const ordering = searchParams.get("order") || "updated_desc";
  const [items, setItems] = useState<OperatorWorkflowSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const updateParam = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams);
    value ? next.set(key, value) : next.delete(key);
    setSearchParams(next, { replace: true });
  };

  const load = useCallback(async (signal?: AbortSignal) => {
    const next = await operatorApi.listWorkflows({ state, search, ordering }, signal);
    setItems(next);
    setError(null);
    setLoading(false);
  }, [state, search, ordering]);

  useEffect(() => startSerialPolling(load, {
    intervalMs: 10_000,
    maxBackoffMs: 60_000,
    onError: (reason) => { setError(reason instanceof Error ? reason.message : "Falha ao carregar produções"); setLoading(false); },
  }), [load]);

  return <div className="page">
    <PageHeader title="Produções" description="Fila operacional baseada no snapshot persistido de cada roteiro." actions={<Link className="primary-link" to="/productions/new">Iniciar nova produção</Link>} />
    <div className="filters" role="search">
      <label>Buscar por título ou UUID<input value={search} onChange={(event) => updateParam("q", event.target.value)} /></label>
      <label>Estado<select value={state} onChange={(event) => updateParam("state", event.target.value)}>{productionStates.map((value) => <option key={value} value={value}>{value ? value.replace(/_/g, " ") : "todos"}</option>)}</select></label>
      <label>Ordenar<select value={ordering} onChange={(event) => updateParam("order", event.target.value)}><option value="updated_desc">Atualização recente</option><option value="created_desc">Criação recente</option><option value="title">Título</option></select></label>
    </div>
    {error && <Alert tone="warning">{error}{items.length ? " — exibindo último snapshot." : ""}</Alert>}
    {loading && !items.length ? <LoadingState /> : items.length === 0 ? <EmptyState title="Nenhuma produção encontrada" detail="Ajuste os filtros ou inicie uma nova produção." /> : <TableWrap><table>
      <thead><tr><th>Produção</th><th>Status / fase</th><th>Atenção</th><th>QA</th><th>Revisão humana</th><th>Duração</th><th>Atualizada</th></tr></thead>
      <tbody>{items.map((item) => <tr key={item.id}>
        <td><Link to={workflowPath(item.id)}><strong>{item.title}</strong></Link><small className="block-meta">{item.id} · {item.production_profile || "sem perfil"}</small></td>
        <td><StatusBadge value={item.production_status || item.legacy_status} /><small className="block-meta">{item.production_phase || "áudio legado"}</small></td>
        <td>{item.attention.length ? item.attention.map((reason) => <span className="block-meta attention-text" key={reason.code}>{reason.message}</span>) : "—"}</td>
        <td><StatusBadge value={item.qa?.status || "não executado"} /></td>
        <td><StatusBadge value={item.human_review.state} /></td>
        <td>{item.audio_duration_seconds?.toFixed(1) || item.script_duration_seconds?.toFixed(0) || "—"}s</td>
        <td>{new Date(item.updated_at).toLocaleString("pt-BR")}</td>
      </tr>)}</tbody>
    </table></TableWrap>}
  </div>;
}
