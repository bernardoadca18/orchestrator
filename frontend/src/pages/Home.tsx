import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, RoteiroDisponivel, Workflow } from "../api/client";

export default function Home() {
  const navigate = useNavigate();
  const [roteiros, setRoteiros] = useState<RoteiroDisponivel[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [filtroStatus, setFiltroStatus] = useState<string>("");
  const [erro, setErro] = useState<string | null>(null);

  async function carregar() {
    try {
      const [r, w] = await Promise.all([
        api.listarRoteirosDisponiveis(),
        api.listarWorkflows(filtroStatus || undefined),
      ]);
      setRoteiros(r);
      setWorkflows(w);
      setErro(null);
    } catch (e) {
      setErro((e as Error).message);
    }
  }

  useEffect(() => {
    carregar();
    const interval = setInterval(carregar, 5000);
    return () => clearInterval(interval);
  }, [filtroStatus]);

  const idsEmWorkflow = new Set(workflows.map((w) => w.roteiro_id));

  const statusOptions = ["", "agendada", "gerando_audio", "audio_concluido", "erro", "cancelada"];

  const statusColors: Record<string, string> = {
    agendada: "#6c757d",
    gerando_audio: "#007bff",
    audio_concluido: "#28a745",
    erro: "#dc3545",
    cancelada: "#ffc107",
  };

  return (
    <div style={{ fontFamily: "sans-serif", maxWidth: 1100, margin: "2rem auto", padding: "0 1rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "2rem" }}>
        <h1 style={{ margin: 0 }}>Fila de Produção</h1>
        <button onClick={() => navigate("/dashboard")} style={{ padding: "0.5rem 1rem" }}>
          📊 Dashboard
        </button>
      </div>

      {erro && <p style={{ color: "crimson" }}>Erro: {erro}</p>}

      <h2>Roteiros aprovados (ideas-hub)</h2>
      <ul style={{ listStyle: "none", padding: 0 }}>
        {roteiros
          .filter((r) => !idsEmWorkflow.has(r.id))
          .map((r) => (
            <li key={r.id} style={{ marginBottom: "0.5rem", padding: "0.75rem", background: "#f9f9f9", borderRadius: "4px" }}>
              <strong>{r.titulo}</strong> — {r.num_blocos} blocos, ~{r.duracao_total_segundos}s{" "}
              <button onClick={() => api.criarWorkflow(r.id).then(carregar)} style={{ marginLeft: "1rem" }}>
                Gerar áudio
              </button>
            </li>
          ))}
        {roteiros.filter((r) => !idsEmWorkflow.has(r.id)).length === 0 && (
          <p style={{ color: "#666" }}>Nenhum roteiro novo disponível.</p>
        )}
      </ul>

      <h2>Workflows</h2>

      <div style={{ marginBottom: "1rem" }}>
        <label htmlFor="filtro-status" style={{ marginRight: "0.5rem" }}><strong>Filtrar por status:</strong></label>
        <select
          id="filtro-status"
          value={filtroStatus}
          onChange={(e) => setFiltroStatus(e.target.value)}
          style={{ padding: "0.5rem" }}
        >
          {statusOptions.map((s) => (
            <option key={s} value={s}>
              {s || "Todos"}
            </option>
          ))}
        </select>
      </div>

      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ textAlign: "left", borderBottom: "1px solid #ccc" }}>
            <th style={{ padding: "8px" }}>ID</th>
            <th style={{ padding: "8px" }}>Status</th>
            <th style={{ padding: "8px" }}>Duração</th>
            <th style={{ padding: "8px" }}>Tentativas</th>
            <th style={{ padding: "8px" }}>Ações</th>
          </tr>
        </thead>
        <tbody>
          {workflows.map((w) => (
            <tr key={w.id} style={{ borderBottom: "1px solid #eee", cursor: "pointer" }} onClick={() => navigate(`/workflow/${w.id}`)}>
              <td style={{ padding: "8px" }}>{w.id.slice(0, 8)}...</td>
              <td style={{ padding: "8px" }}>
                <span style={{ color: statusColors[w.status] || "#000" }}>
                  {w.status}{w.erro_mensagem ? ` — ${w.erro_mensagem}` : ""}
                </span>
              </td>
              <td style={{ padding: "8px" }}>{w.duracao_audio_segundos ? `${w.duracao_audio_segundos.toFixed(1)}s` : "—"}</td>
              <td style={{ padding: "8px" }}>{w.tentativas}</td>
              <td style={{ padding: "8px" }} onClick={(e) => e.stopPropagation()}>
                {w.status === "audio_concluido" && (
                  <audio controls src={api.urlAudio(w.id)} style={{ height: 30 }} />
                )}
                {w.status === "erro" && (
                  <button onClick={() => api.repetirWorkflow(w.id).then(carregar)}>Repetir</button>
                )}
                {(w.status === "agendada" || w.status === "gerando_audio") && (
                  <button onClick={() => api.cancelarWorkflow(w.id).then(carregar)}>Cancelar</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {workflows.length === 0 && <p style={{ color: "#666", marginTop: "1rem" }}>Nenhum workflow encontrado.</p>}
    </div>
  );
}
