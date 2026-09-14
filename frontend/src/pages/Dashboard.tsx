import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, DashboardStats, DependencyHealth } from "../api/client";
import { OperatorWorkflowSummary, operatorApi } from "../api/operator";
import { workflowPath } from "../app/routes";
import { Alert, Card, EmptyState, LoadingState, PageHeader, StatusBadge } from "../components/ui";
import { startSerialPolling } from "../utils/serialPolling";

function seconds(value: number | null) {
  return value == null ? "—" : `${value.toFixed(1)}s`;
}

export default function Dashboard() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [health, setHealth] = useState<DependencyHealth | null>(null);
  const [workflows, setWorkflows] = useState<OperatorWorkflowSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  useEffect(() => startSerialPolling(async (signal) => {
    const [nextStats, nextHealth, nextWorkflows] = await Promise.all([
      api.obterDashboardStats(signal), api.obterDependencias(signal), operatorApi.listWorkflows({ limit: 100 }, signal),
    ]);
    setStats(nextStats); setHealth(nextHealth); setWorkflows(nextWorkflows); setError(null); setUpdatedAt(new Date());
  }, { intervalMs: 10_000, maxBackoffMs: 60_000, onError: (reason) => setError(reason instanceof Error ? reason.message : "Falha ao atualizar dashboard") }), []);
  if (!stats && error) return <div className="page"><Alert tone="danger">{error}</Alert></div>;
  if (!stats) return <div className="page"><LoadingState label="Carregando dashboard…" /></div>;
  const attention = workflows.filter((workflow) => workflow.attention.length > 0);
  const needsReview = workflows.filter((workflow) => ["pending", "stale", "changes_requested", "rejected"].includes(workflow.human_review.state));
  const active = workflows.filter((workflow) => ["queued", "running"].includes(workflow.production_status || ""));
  const cards = [
    ["Fila Celery", stats.fila_status === "available" ? String(stats.fila_tamanho) : "Indisponível", stats.fila_status === "available" ? stats.fila_nome : `Medição falhou (${stats.fila_error || "erro desconhecido"})`],
    ["Trabalho ativo", String(active.length), "Snapshot do Operator Console"],
    ["Requer atenção", String(attention.length), "Sem tratar completed como erro"],
    ["Aguardando revisão", String(needsReview.length), "Render-bound Human Review"],
    ["Latency p50 / p95", `${seconds(stats.generation_latency_p50_seconds)} / ${seconds(stats.generation_latency_p95_seconds)}`, "Geração de takes nas últimas 24h"],
    ["Render factor", stats.render_factor_mean_24h?.toFixed(3) || "—", "Tempo de render / duração"],
    ["QA aprovado", stats.qa_success_rate_24h_percent == null ? "—" : `${stats.qa_success_rate_24h_percent.toFixed(1)}%`, "passed + passed_with_warnings"],
  ];
  return <div className="page">
    <PageHeader title="Dashboard operacional" description="Saúde, trabalho ativo e intervenções. A gestão completa fica em Produções." actions={<Link className="secondary-link" to="/productions">Abrir produções</Link>} />
    {error && <Alert tone="warning">Última atualização falhou: {error}. Exibindo snapshot anterior.</Alert>}
    <Card title="Dependências"><div className="dependency-grid">{health && Object.entries(health.dependencies).map(([name, dependency]) => <div className="dependency-row" key={name}><strong>{name.replace(/_/g, " ")}</strong><StatusBadge value={dependency.status} /><small>{dependency.latency_ms == null ? dependency.error : `${dependency.latency_ms.toFixed(1)} ms`}</small></div>)}</div></Card>
    <div className="metrics-grid">{cards.map(([title, value, note]) => <Card key={title}><h3>{title}</h3><strong className="metric-value">{value}</strong><p>{note}</p></Card>)}</div>
    <Card title="Requer atenção">{attention.length === 0 ? <EmptyState title="Nenhuma intervenção necessária" /> : <div className="attention-list">{attention.slice(0, 12).map((workflow) => <Link to={workflowPath(workflow.id)} key={workflow.id}><strong>{workflow.title}</strong><span>{workflow.attention.map((reason) => reason.message).join(" ")}</span><small>{workflow.production_phase || workflow.legacy_status}</small></Link>)}</div>}</Card>
    <footer className="page-footer">Atualização serial sem requests sobrepostas · {updatedAt?.toLocaleTimeString("pt-BR") || "—"}</footer>
  </div>;
}
