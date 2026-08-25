import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api, Workflow, WorkflowRender } from "../api/client";

export default function WorkflowDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [render, setRender] = useState<WorkflowRender | null>(null);

  async function carregar() {
    if (!id) return;
    try {
      const wf = await api.obterWorkflow(id);
      setWorkflow(wf);
      const currentRender = await api.obterRender(id).catch(() => null);
      setRender(currentRender);
      setErro(null);
    } catch (e) {
      setErro((e as Error).message);
    }
  }

  useEffect(() => {
    carregar();
    const interval = setInterval(carregar, 3000); // Poll a cada 3s
    return () => clearInterval(interval);
  }, [id]);

  if (erro) return <div style={{ color: "crimson", padding: "2rem" }}>Erro: {erro}</div>;
  if (!workflow) return <div style={{ padding: "2rem" }}>Carregando...</div>;

  const statusColors: Record<string, string> = {
    agendada: "#6c757d",
    gerando_audio: "#007bff",
    audio_concluido: "#28a745",
    erro: "#dc3545",
    cancelada: "#ffc107",
  };

  return (
    <div style={{ fontFamily: "sans-serif", maxWidth: 1000, margin: "2rem auto", padding: "0 1rem" }}>
      <button onClick={() => navigate("/")} style={{ marginBottom: "1rem" }}>
        ← Voltar
      </button>

      <h1>Workflow {workflow.id.slice(0, 8)}...</h1>

      <div style={{ marginBottom: "2rem", padding: "1rem", background: "#f5f5f5", borderRadius: "8px" }}>
        <p><strong>Status:</strong> <span style={{ color: statusColors[workflow.status] }}>{workflow.status}</span></p>
        <p><strong>Perfil de Produção:</strong> {workflow.perfil_producao || "—"}</p>
        <p><strong>Tentativas:</strong> {workflow.tentativas}</p>
        <p><strong>Fase de áudio:</strong> {workflow.audio_phase}</p>
        <p><strong>Produção:</strong> {workflow.production_status || "legado / inativa"}</p>
        <p><strong>Fase de produção:</strong> {workflow.production_phase || "—"}</p>
        <p><strong>Duração Total:</strong> {workflow.duracao_audio_segundos ? `${workflow.duracao_audio_segundos.toFixed(1)}s` : "—"}</p>
        <p><strong>Formato final:</strong> {workflow.audio_final_sample_rate ? `${workflow.audio_final_sample_rate} Hz / ${workflow.audio_final_channels} canal` : "—"}</p>

        {workflow.erro_mensagem && (
          <p style={{ color: "crimson" }}><strong>Erro:</strong> {workflow.erro_mensagem}</p>
        )}
        {workflow.production_error_message && (
          <p style={{ color: "crimson" }}><strong>Erro de produção:</strong> {workflow.production_error_category}: {workflow.production_error_message}</p>
        )}

        {render && (
          <div style={{ marginTop: "1rem" }}>
            <p><strong>Render:</strong> {render.status} / {render.phase} ({Math.round(render.progress * 100)}%)</p>
            {render.total_frames && <p><strong>Frames:</strong> {render.frames_rendered}/{render.total_frames}</p>}
            {render.error && <p style={{ color: "crimson" }}>{render.error.category}: {render.error.message}</p>}
            {render.status === "completed" && (
              <a href={api.urlVideo(workflow.id)} download>Baixar vídeo final</a>
            )}
          </div>
        )}

        {workflow.total_blocos !== undefined && (
          <div style={{ marginTop: "1rem" }}>
            <p><strong>Progresso:</strong></p>
            <ul>
              <li>Total de blocos: {workflow.total_blocos}</li>
              <li>Concluídos: {workflow.blocos_concluidos}</li>
              <li>Pendentes: {workflow.blocos_pendentes}</li>
              <li>Gerando: {workflow.blocos_gerando}</li>
              <li>Com erro: {workflow.blocos_erro}</li>
            </ul>
          </div>
        )}

        <div style={{ marginTop: "1rem" }}>
          {workflow.status === "audio_concluido" && workflow.audio_available && (
            <div>
              <p><strong>Áudio Final:</strong></p>
              <audio controls src={api.urlAudio(workflow.id)} style={{ width: "100%", maxWidth: "500px" }} />
            </div>
          )}

          {workflow.status === "erro" && (
            <button onClick={() => api.repetirWorkflow(workflow.id).then(carregar)}>
              Repetir Workflow
            </button>
          )}

          {workflow.production_status === "failed" && workflow.status !== "erro" && (
            <button onClick={() => api.repetirWorkflow(workflow.id).then(carregar)}>
              Repetir fase de produção
            </button>
          )}

          {(workflow.production_status === "blocked" || workflow.production_status === null) && (
            <button onClick={() => api.retomarProducao(workflow.id).then(carregar)}>
              Retomar produção
            </button>
          )}

          {(workflow.production_status === "queued" || workflow.production_status === "running") && (
            <button onClick={() => api.cancelarWorkflow(workflow.id).then(carregar)}>
              Cancelar
            </button>
          )}
        </div>
      </div>

      <h2>Blocos de Áudio</h2>
      {workflow.blocos && workflow.blocos.length > 0 ? (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "2px solid #ccc" }}>
              <th style={{ padding: "8px" }}>#</th>
              <th style={{ padding: "8px" }}>Tipo</th>
              <th style={{ padding: "8px" }}>Status</th>
              <th style={{ padding: "8px" }}>Duração</th>
              <th style={{ padding: "8px" }}>Voz / geração</th>
              <th style={{ padding: "8px" }}>Seed</th>
              <th style={{ padding: "8px" }}>Erro</th>
              <th style={{ padding: "8px" }}>Áudio</th>
            </tr>
          </thead>
          <tbody>
            {workflow.blocos.map((bloco) => (
              <tr key={bloco.id} style={{ borderBottom: "1px solid #eee" }}>
                <td style={{ padding: "8px" }}>{bloco.ordem + 1}</td>
                <td style={{ padding: "8px" }}>{bloco.tipo}</td>
                <td style={{ padding: "8px" }}>
                  <span style={{ color: statusColors[bloco.status] || "#000" }}>
                    {bloco.status}
                  </span>
                </td>
                <td style={{ padding: "8px" }}>
                  {bloco.duracao_segundos ? `${bloco.duracao_segundos.toFixed(1)}s` : "—"}
                </td>
                <td style={{ padding: "8px" }}>
                  {bloco.voice_profile || "—"} / {bloco.generation_profile || "—"}
                </td>
                <td style={{ padding: "8px" }}>{bloco.resolved_seed ?? "—"}</td>
                <td style={{ padding: "8px", color: "crimson" }}>
                  {bloco.erro_mensagem || "—"}
                </td>
                <td style={{ padding: "8px" }}>
                  {bloco.status === "concluido" && bloco.audio_sha256 && (
                    <audio controls src={api.urlAudioBloco(workflow.id, bloco.id)} style={{ height: 30 }} />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p>Nenhum bloco encontrado.</p>
      )}
    </div>
  );
}
