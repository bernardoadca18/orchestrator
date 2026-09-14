import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, DashboardStats, DependencyHealth } from "../api/client";
import { OperatorWorkflowSummary, operatorApi } from "../api/operator";
import { workflowPath } from "../app/routes";
import { Alert, EmptyState, LoadingState, StatusBadge, CopyButton, RelativeTime } from "../components/ui";
import { startSerialPolling } from "../utils/serialPolling";
import { isActiveProductionStatus } from "./dashboardState";

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
      api.obterDashboardStats(signal),
      api.obterDependencias(signal),
      operatorApi.listWorkflows({ limit: 100 }, signal),
    ]);
    setStats(nextStats);
    setHealth(nextHealth);
    setWorkflows(nextWorkflows);
    setError(null);
    setUpdatedAt(new Date());
  }, {
    intervalMs: 10_000,
    maxBackoffMs: 60_000,
    onError: (reason) => setError(reason instanceof Error ? reason.message : "Falha ao atualizar dashboard"),
  }), []);

  if (!stats && error) return <div className="page"><Alert tone="danger">{error}</Alert></div>;
  if (!stats) return <div className="page"><LoadingState label="Carregando dashboard…" /></div>;

  const attention = workflows.filter((w) => w.attention.length > 0);
  const needsReview = workflows.filter((w) => w.human_review.state === "pending" || w.human_review.state === "stale");
  const active = workflows.filter((w) => isActiveProductionStatus(w.production_status));

  return <div className="page">
    {error && <Alert tone="warning">{error}</Alert>}

    <div className="dashboard-grid">
      {/* Primary: Active Production */}
      <section className="dashboard-section span-2">
        <h2>Produção ativa</h2>
        {active.length === 0 ? (
          <EmptyState title="Nenhuma produção ativa" detail="Produções em processamento aparecerão aqui." />
        ) : (
          <div className="dashboard-primary">
            {active.slice(0, 5).map((w) => (
              <div className="dashboard-item" key={w.id}>
                <Link className="dashboard-item-main" to={workflowPath(w.id)}>
                  <span className="dashboard-item-title">{w.title}</span>
                  <span className="dashboard-item-meta">
                    {w.production_phase || "legado"}
                  </span>
                </Link>
                <div className="dashboard-item-value">
                  <StatusBadge value={w.production_status || w.legacy_status} />
                  <CopyButton value={w.id} label="UUID" />
                </div>
              </div>
            ))}
            {active.length > 5 && (
              <Link to="/productions?state=active" className="primary-link">Ver todas ({active.length})</Link>
            )}
          </div>
        )}
      </section>

      {/* Primary: Needs Attention */}
      <section className="dashboard-section">
        <h2>Atenção</h2>
        {attention.length === 0 ? (
          <div className="dashboard-item">
            <span className="text-muted">Nenhum item</span>
            <StatusBadge value="healthy" />
          </div>
        ) : (
          <div className="dashboard-primary">
            {attention.slice(0, 4).map((w) => (
              <Link className="dashboard-item" key={w.id} to={workflowPath(w.id)}>
                <div className="dashboard-item-main">
                  <span className="dashboard-item-title">{w.title}</span>
                  <span className="dashboard-item-meta">{w.attention[0]?.message}</span>
                </div>
                <StatusBadge value="needs_attention" />
              </Link>
            ))}
            {attention.length > 4 && (
              <Link to="/productions?state=needs_attention" className="primary-link">Ver todas ({attention.length})</Link>
            )}
          </div>
        )}
      </section>

      {/* Secondary: Needs Review */}
      <section className="dashboard-section">
        <h2>Revisão pendente</h2>
        {needsReview.length === 0 ? (
          <div className="dashboard-item">
            <span className="text-muted">Nenhum item</span>
          </div>
        ) : (
          <div className="dashboard-primary">
            {needsReview.slice(0, 3).map((w) => (
              <Link className="dashboard-item" key={w.id} to={workflowPath(w.id, "render")}>
                <div className="dashboard-item-main">
                  <span className="dashboard-item-title">{w.title}</span>
                  <span className="dashboard-item-meta">
                    {w.human_review.state === "stale" ? "Aprovação histórica" : "Aguardando revisão"}
                  </span>
                </div>
                <StatusBadge value={w.human_review.state} />
              </Link>
            ))}
            {needsReview.length > 3 && (
              <Link to="/review" className="primary-link">Ver fila ({needsReview.length})</Link>
            )}
          </div>
        )}
      </section>

      {/* Secondary: System Health (compact) */}
      <section className="dashboard-section span-2">
        <h2>Saúde do sistema</h2>
        <div className="dashboard-health-compact">
          {health ? (
            Object.entries(health.dependencies).map(([name, dep]) => (
              <div key={name} className="health-compact-item">
                <span>{name}</span>
                <StatusBadge value={dep.status} />
              </div>
            ))
          ) : (
            <span className="text-muted">Indisponível</span>
          )}
        </div>
      </section>

      {/* Metrics */}
      <section className="dashboard-section">
        <h2>Métricas</h2>
        <div className="dashboard-primary">
          <div className="dashboard-item">
            <span className="dashboard-item-title">Workflows ativos</span>
            <span className="dashboard-item-value">{stats.workflows_ativos}</span>
          </div>
          <div className="dashboard-item">
            <span className="dashboard-item-title">Latência P50</span>
            <span className="dashboard-item-value">{seconds(stats.generation_latency_p50_seconds)}</span>
          </div>
          <div className="dashboard-item">
            <span className="dashboard-item-title">Render factor</span>
            <span className="dashboard-item-value">{stats.render_factor_mean_24h?.toFixed(2) || "—"}</span>
          </div>
        </div>
      </section>
    </div>

    {updatedAt && (
      <p className="text-muted" style={{ marginTop: "1rem", fontSize: "0.8rem" }}>
        Atualizado <RelativeTime date={updatedAt} />
      </p>
    )}
  </div>;
}
