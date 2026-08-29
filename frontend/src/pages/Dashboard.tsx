import { useEffect, useState } from "react";
import { api, DashboardStats, DependencyHealth, Workflow } from "../api/client";
import { startSerialPolling } from "../utils/serialPolling";

function attentionReason(workflow: Workflow): string | null {
  if (workflow.production_status === "blocked") return "Produção bloqueada";
  if (workflow.production_status === "failed") return "Falha na produção";
  if (workflow.status === "erro") return "Falha no áudio";
  if (workflow.production_status === "completed") return "Concluído: revisar QA/publicação";
  return null;
}

function seconds(value: number | null) {
  return value == null ? "—" : `${value.toFixed(1)}s`;
}

export default function Dashboard() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [health, setHealth] = useState<DependencyHealth | null>(null);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  useEffect(() => startSerialPolling(async (signal) => {
    const [nextStats, nextHealth, nextWorkflows] = await Promise.all([
      api.obterDashboardStats(signal),
      api.obterDependencias(signal),
      api.listarWorkflows(undefined, signal),
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

  if (!stats && error) return <div className="ops-error">Erro operacional: {error}</div>;
  if (!stats) return <div className="ops-page">Carregando dashboard…</div>;
  const attention = workflows.map((workflow) => ({ workflow, reason: attentionReason(workflow) })).filter((item) => item.reason);

  const cards = [
    ["Fila Celery", stats.fila_status === "available" ? String(stats.fila_tamanho) : "Indisponível", stats.fila_status === "available" ? stats.fila_nome : `Medição falhou (${stats.fila_error || "erro desconhecido"})`],
    ["Workflows ativos", String(stats.workflows_ativos), "queued/running, incluindo compatibilidade legada"],
    ["Duração média do áudio", seconds(stats.duracao_media_audio_bloco_segundos), "Duração física dos WAVs; não é latency"],
    ["Latency de geração p50 / p95", `${seconds(stats.generation_latency_p50_seconds)} / ${seconds(stats.generation_latency_p95_seconds)}`, "Tempo entre criação e conclusão dos takes"],
    ["Retries de qualidade / takes falhos", `${stats.quality_retries_24h} / ${stats.failed_takes_24h}`, "Últimas 24 horas"],
    ["Tentativas médias por bloco", stats.average_attempts_per_completed_block_24h?.toFixed(2) || "—", "Blocos concluídos nas últimas 24 horas"],
    ["Tempo médio de workflow", seconds(stats.workflow_completion_time_mean_seconds_24h), "Stage 17 completo, últimas 24 horas"],
    ["Render factor médio", stats.render_factor_mean_24h?.toFixed(3) || "—", "Tempo de render / duração do vídeo"],
    ["QA aprovado", stats.qa_success_rate_24h_percent == null ? "—" : `${stats.qa_success_rate_24h_percent.toFixed(1)}%`, "passed + passed_with_warnings"],
  ];

  return <div className="ops-page">
    <div className="ops-heading"><div><h1>Console de Produção</h1><p>Estado operacional baseado em dados duráveis.</p></div><span className={`health-pill ${health?.status || "degraded"}`}>{health?.status || "carregando"}</span></div>
    {error && <div className="ops-warning">Última atualização falhou: {error}. Exibindo o último snapshot válido.</div>}

    <section><h2>Dependências</h2><div className="dependency-grid">
      {health && Object.entries(health.dependencies).map(([name, dependency]) => <div className="dependency-row" key={name}><strong>{name.replace(/_/g, " ")}</strong><span className={dependency.status}>{dependency.status}</span><small>{dependency.latency_ms == null ? dependency.error : `${dependency.latency_ms.toFixed(1)} ms`}</small></div>)}
    </div></section>

    <section><h2>Métricas confiáveis (24h)</h2><div className="metrics-grid">{cards.map(([title, value, note]) => <div className="metric-card" key={title}><h3>{title}</h3><strong>{value}</strong><p>{note}</p></div>)}</div></section>

    <section><h2>Requer atenção</h2>{attention.length === 0 ? <p className="empty-ops">Nenhum workflow exige ação neste snapshot.</p> : <div className="attention-list">{attention.map(({ workflow, reason }) => <a href={`/workflow/${workflow.id}`} key={workflow.id}><strong>{workflow.id.slice(0, 8)}…</strong><span>{reason}</span><small>{workflow.production_phase || workflow.audio_phase}</small></a>)}</div>}</section>
    <footer>Atualização serial, sem requests sobrepostas. Último snapshot: {updatedAt?.toLocaleTimeString("pt-BR") || "—"}</footer>
  </div>;
}
