const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8011";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `Erro ${resp.status}`);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json();
}

export interface RoteiroDisponivel {
  id: string;
  ideia_id: string;
  versao: number;
  titulo: string;
  duracao_total_segundos: number;
  num_blocos: number;
}

export interface AudioBloco {
  id: string;
  ordem: number;
  tipo: string;
  status: string;
  duracao_segundos: number | null;
  erro_mensagem: string | null;
}

export interface Workflow {
  id: string;
  ideia_id: string;
  roteiro_id: string;
  status: string;
  audio_final_path: string | null;
  duracao_audio_segundos: number | null;
  erro_mensagem: string | null;
  tentativas: number;
  criado_em: string;
  atualizado_em: string;
  blocos?: AudioBloco[];
}

export const api = {
  listarRoteirosDisponiveis: () => request<RoteiroDisponivel[]>("/workflow/roteiros-disponiveis"),
  listarWorkflows: () => request<Workflow[]>("/workflow"),
  obterWorkflow: (id: string) => request<Workflow>(`/workflow/${id}`),
  criarWorkflow: (roteiro_id: string) =>
    request<Workflow>("/workflow", { method: "POST", body: JSON.stringify({ roteiro_id }) }),
  repetirWorkflow: (id: string) => request<Workflow>(`/workflow/${id}/retry`, { method: "PATCH" }),
  cancelarWorkflow: (id: string) => request<Workflow>(`/workflow/${id}/cancelar`, { method: "PATCH" }),
  urlAudio: (id: string) => `${BASE_URL}/workflow/${id}/audio`,
};
