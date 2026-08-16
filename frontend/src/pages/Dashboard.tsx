import { useEffect, useState } from "react";
import { DashboardStats } from "../api/client";
import { api } from "../api/client";

export default function Dashboard() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [erro, setErro] = useState<string | null>(null);

  async function carregar() {
    try {
      const data = await api.obterDashboardStats();
      setStats(data);
      setErro(null);
    } catch (e) {
      setErro((e as Error).message);
    }
  }

  useEffect(() => {
    carregar();
    const interval = setInterval(carregar, 10000); // Atualiza a cada 10s
    return () => clearInterval(interval);
  }, []);

  if (erro) return <div style={{ color: "crimson", padding: "2rem" }}>Erro: {erro}</div>;
  if (!stats) return <div style={{ padding: "2rem" }}>Carregando dashboard...</div>;

  return (
    <div style={{ fontFamily: "sans-serif", maxWidth: 900, margin: "2rem auto", padding: "0 1rem" }}>
      <h1>Dashboard de Produção</h1>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(250px, 1fr))", gap: "1rem", marginTop: "2rem" }}>
        <div style={{ padding: "1.5rem", background: "#e3f2fd", borderRadius: "8px" }}>
          <h3 style={{ margin: "0 0 0.5rem 0", color: "#1976d2" }}>Fila de Produção</h3>
          <p style={{ fontSize: "2rem", margin: 0, fontWeight: "bold" }}>{stats.fila_tamanho}</p>
          <p style={{ fontSize: "0.875rem", margin: "0.5rem 0 0 0", color: "#666" }}>
            Workflows aguardando processamento
          </p>
        </div>

        <div style={{ padding: "1.5rem", background: "#fff3e0", borderRadius: "8px" }}>
          <h3 style={{ margin: "0 0 0.5rem 0", color: "#f57c00" }}>Workflows Ativos</h3>
          <p style={{ fontSize: "2rem", margin: 0, fontWeight: "bold" }}>{stats.workflows_ativos}</p>
          <p style={{ fontSize: "0.875rem", margin: "0.5rem 0 0 0", color: "#666" }}>
            Em andamento agora
          </p>
        </div>

        <div style={{ padding: "1.5rem", background: "#e8f5e9", borderRadius: "8px" }}>
          <h3 style={{ margin: "0 0 0.5rem 0", color: "#388e3c" }}>Blocos Concluídos (24h)</h3>
          <p style={{ fontSize: "2rem", margin: 0, fontWeight: "bold" }}>{stats.blocos_concluidos_24h}</p>
          <p style={{ fontSize: "0.875rem", margin: "0.5rem 0 0 0", color: "#666" }}>
            Áudios gerados com sucesso
          </p>
        </div>

        <div style={{ padding: "1.5rem", background: "#ffebee", borderRadius: "8px" }}>
          <h3 style={{ margin: "0 0 0.5rem 0", color: "#d32f2f" }}>Blocos com Erro (24h)</h3>
          <p style={{ fontSize: "2rem", margin: 0, fontWeight: "bold" }}>{stats.blocos_erro_24h}</p>
          <p style={{ fontSize: "0.875rem", margin: "0.5rem 0 0 0", color: "#666" }}>
            Falhas no processamento
          </p>
        </div>

        <div style={{ padding: "1.5rem", background: "#f3e5f5", borderRadius: "8px" }}>
          <h3 style={{ margin: "0 0 0.5rem 0", color: "#7b1fa2" }}>Tempo Médio por Bloco</h3>
          <p style={{ fontSize: "2rem", margin: 0, fontWeight: "bold" }}>
            {stats.tempo_medio_bloco_segundos ? `${stats.tempo_medio_bloco_segundos.toFixed(1)}s` : "—"}
          </p>
          <p style={{ fontSize: "0.875rem", margin: "0.5rem 0 0 0", color: "#666" }}>
            Performance média (24h)
          </p>
        </div>

        <div style={{ padding: "1.5rem", background: stats.taxa_erro_24h_percent > 10 ? "#ffebee" : "#e8f5e9", borderRadius: "8px" }}>
          <h3 style={{ margin: "0 0 0.5rem 0", color: stats.taxa_erro_24h_percent > 10 ? "#d32f2f" : "#388e3c" }}>
            Taxa de Erro (24h)
          </h3>
          <p style={{ fontSize: "2rem", margin: 0, fontWeight: "bold" }}>{stats.taxa_erro_24h_percent.toFixed(1)}%</p>
          <p style={{ fontSize: "0.875rem", margin: "0.5rem 0 0 0", color: "#666" }}>
            {stats.taxa_erro_24h_percent > 10 ? "⚠️ Acima do esperado" : "✓ Dentro do esperado"}
          </p>
        </div>
      </div>

      <div style={{ marginTop: "2rem", padding: "1rem", background: "#f5f5f5", borderRadius: "8px" }}>
        <p style={{ fontSize: "0.875rem", color: "#666", margin: 0 }}>
          Dados atualizados automaticamente a cada 10 segundos. Última atualização: {new Date().toLocaleTimeString("pt-BR")}
        </p>
      </div>
    </div>
  );
}
