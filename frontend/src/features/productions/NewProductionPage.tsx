import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, RoteiroDisponivel } from "../../api/client";
import { Alert, Card, EmptyState, LoadingState, PageHeader } from "../../components/ui";
import { SingleFlightAction } from "../../data/operatorAction";

export default function NewProductionPage() {
  const navigate = useNavigate();
  const [scripts, setScripts] = useState<RoteiroDisponivel[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [flight] = useState(() => new SingleFlightAction());
  useEffect(() => {
    const controller = new AbortController();
    api.listarRoteirosDisponiveis(controller.signal).then(setScripts).catch((reason) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Falha ao buscar roteiros");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, []);

  async function create(script: RoteiroDisponivel) {
    setSubmitting(script.id);
    setError(null);
    try {
      const workflow = await flight.run(() => api.criarWorkflow(script.id));
      navigate(`/productions/${workflow.id}/overview`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Falha ao iniciar produção");
    } finally {
      setSubmitting(null);
    }
  }

  return <div className="page narrow-page">
    <PageHeader title="Iniciar nova produção" description="Somente roteiros aprovados e production-ready são aceitos pelo backend." />
    {error && <Alert tone="danger">{error}</Alert>}
    {loading ? <LoadingState /> : scripts.length === 0 ? <EmptyState title="Nenhum roteiro disponível" detail="O Ideas Hub pode estar offline ou não há roteiros aprovados." /> : <div className="card-list">{scripts.map((script) => <Card key={script.id}>
      <div className="split-row"><div><h2>{script.titulo}</h2><p>{script.num_blocos} blocos · ~{script.duracao_total_segundos}s · versão {script.versao}</p></div><button disabled={submitting !== null} onClick={() => void create(script)}>{submitting === script.id ? "Iniciando…" : "Iniciar produção"}</button></div>
    </Card>)}</div>}
  </div>;
}
