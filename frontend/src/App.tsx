import { useEffect, useState } from "react";
import { api, RoteiroDisponivel, Workflow } from "./api/client";

export default function App() {
  const [roteiros, setRoteiros] = useState<RoteiroDisponivel[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [erro, setErro] = useState<string | null>(null);

  async function carregar() {
    try {
      const [r, w] = await Promise.all([api.listarRoteirosDisponiveis(), api.listarWorkflows()]);
      setRoteiros(r);
      setWorkflows(w);
      setErro(null);
    } catch (e) {
      setErro((e as Error).message);
    }
  }

  useEffect(() => {
    carregar();
    const interval = setInterval(carregar, 5000); // poll simples de progresso
    return () => clearInterval(interval);
  }, []);

  const idsEmWorkflow = new Set(workflows.map((w) => w.roteiro_id));

  return (
    <div style={{ fontFamily: "sans-serif", maxWidth: 900, margin: "2rem auto", padding: "0 1rem" }}>
      <h1>Fila de Produção</h1>
      {erro && <p style={{ color: "crimson" }}>Erro: {erro}</p>}

      <h2>Roteiros aprovados (ideas-hub)</h2>
      <ul>
        {roteiros
          .filter((r) => !idsEmWorkflow.has(r.id))
          .map((r) => (
            <li key={r.id} style={{ marginBottom: 8 }}>
              <strong>{r.titulo}</strong> — {r.num_blocos} blocos, ~{r.duracao_total_segundos}s{" "}
              <button onClick={() => api.criarWorkflow(r.id).then(carregar)}>Gerar áudio</button>
            </li>
          ))}
        {roteiros.filter((r) => !idsEmWorkflow.has(r.id)).length === 0 && <p>Nenhum roteiro novo disponível.</p>}
      </ul>

      <h2>Workflows</h2>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ textAlign: "left", borderBottom: "1px solid #ccc" }}>
            <th>Status</th>
            <th>Duração</th>
            <th>Tentativas</th>
            <th>Ações</th>
          </tr>
        </thead>
        <tbody>
          {workflows.map((w) => (
            <tr key={w.id} style={{ borderBottom: "1px solid #eee" }}>
              <td>{w.status}{w.erro_mensagem ? ` — ${w.erro_mensagem}` : ""}</td>
              <td>{w.duracao_audio_segundos ? `${w.duracao_audio_segundos.toFixed(1)}s` : "—"}</td>
              <td>{w.tentativas}</td>
              <td>
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
    </div>
  );
}
