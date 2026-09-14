import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ReviewQueueItem, operatorApi } from "../../api/operator";
import { workflowPath } from "../../app/routes";
import { Alert, EmptyState, LoadingState, PageHeader, StatusBadge, TableWrap } from "../../components/ui";
import { startSerialPolling } from "../../utils/serialPolling";

export default function ReviewQueuePage() {
  const [items, setItems] = useState<ReviewQueueItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => startSerialPolling(async (signal) => {
    setItems(await operatorApi.getReviewQueue(signal)); setError(null); setLoading(false);
  }, { intervalMs: 10_000, maxBackoffMs: 60_000, onError: (reason) => { setError(reason instanceof Error ? reason.message : "Falha ao carregar revisão"); setLoading(false); } }), []);
  return <div className="page"><PageHeader title="Fila de revisão" description="Somente itens que exigem intervenção humana, priorizados pelo backend." />
    {error && <Alert tone="warning">{error}</Alert>}
    {loading ? <LoadingState /> : items.length === 0 ? <EmptyState title="Nenhuma revisão pendente" /> : <TableWrap><table><thead><tr><th>Prioridade</th><th>Produção</th><th>Categoria</th><th>QA</th><th>Human Review</th><th>Motivo</th></tr></thead><tbody>{items.map((item) => <tr key={`${item.workflow.id}:${item.category}`}><td>{item.priority}</td><td><Link to={workflowPath(item.workflow.id, item.category === "audio_attention" ? "narration" : "render")}><strong>{item.workflow.title}</strong></Link><small className="block-meta">{item.workflow.id}</small></td><td><StatusBadge value={item.category} /></td><td><StatusBadge value={item.workflow.qa?.status || "não executado"} /></td><td><StatusBadge value={item.workflow.human_review.state} /></td><td>{item.workflow.attention.map((reason) => reason.message).join(" ")}</td></tr>)}</tbody></table></TableWrap>}
  </div>;
}
