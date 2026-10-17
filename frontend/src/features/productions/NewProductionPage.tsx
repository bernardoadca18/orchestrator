import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  api,
  MediaProject,
  MediaSession,
  RevisionReadiness,
  RoteiroDisponivel,
} from "../../api/client";
import {
  Alert,
  Card,
  EmptyState,
  LoadingState,
  PageHeader,
  StatusBadge,
} from "../../components/ui";
import { SingleFlightAction } from "../../data/operatorAction";
import {
  canStartProduction,
  defaultRevisionId,
  hasReadinessIssue,
  isHistoricalRevision,
} from "./productionSetup";
import { VisualQueriesActionPanel } from "./VisualQueriesActionPanel";
import {
  hasMissingVisualQueries,
  visualQueriesFailure,
  VisualQueriesPreparationFlight,
} from "./visualQueriesPreparation";

export default function NewProductionPage() {
  const navigate = useNavigate();
  const [scripts, setScripts] = useState<RoteiroDisponivel[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [readiness, setReadiness] = useState<RevisionReadiness | null>(null);
  const [projects, setProjects] = useState<MediaProject[]>([]);
  const [projectId, setProjectId] = useState("");
  const [sessions, setSessions] = useState<MediaSession[]>([]);
  const [sessionIds, setSessionIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [preparing, setPreparing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [visualServiceUnavailable, setVisualServiceUnavailable] =
    useState(false);
  const [flight] = useState(() => new SingleFlightAction());
  const [visualFlight] = useState(() => new VisualQueriesPreparationFlight());
  const selected = useMemo(
    () => scripts.find((script) => script.id === selectedId) ?? null,
    [scripts, selectedId],
  );
  const latestSelectedVersion = selected
    ? Math.max(
        ...scripts
          .filter((script) => script.ideia_id === selected.ideia_id)
          .map((script) => script.versao),
      )
    : null;

  async function loadScripts(preferredId?: string) {
    const items = await api.listarRoteirosDisponiveis();
    setScripts(items);
    setSelectedId(
      preferredId && items.some((item) => item.id === preferredId)
        ? preferredId
        : defaultRevisionId(items),
    );
  }

  useEffect(() => {
    const controller = new AbortController();
    api
      .listarRoteirosDisponiveis(controller.signal)
      .then((items) => {
        setScripts(items);
        setSelectedId(defaultRevisionId(items));
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(String(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setReadiness(null);
      return;
    }
    const controller = new AbortController();
    setReadiness(null);
    api
      .obterReadinessRevisao(selectedId, controller.signal)
      .then(setReadiness)
      .catch((reason) => {
        if (!controller.signal.aborted)
          setError(
            reason instanceof Error
              ? reason.message
              : "Falha ao consultar Production Readiness",
          );
      });
    return () => controller.abort();
  }, [selectedId]);

  useEffect(() => {
    if (!readiness?.production_readiness.media_scope.required) return;
    const controller = new AbortController();
    api
      .listarProjetosMidia(controller.signal)
      .then(setProjects)
      .catch((reason) => {
        if (!controller.signal.aborted) setError(String(reason));
      });
    return () => controller.abort();
  }, [readiness]);

  useEffect(() => {
    setSessions([]);
    setSessionIds([]);
    if (!projectId) return;
    const controller = new AbortController();
    api
      .listarSessoesMidia(projectId, controller.signal)
      .then(setSessions)
      .catch((reason) => {
        if (!controller.signal.aborted)
          setError(
            reason instanceof Error
              ? reason.message
              : "Falha ao carregar sessões de mídia",
          );
      });
    return () => controller.abort();
  }, [projectId]);

  function confirmHistorical(
    script: RoteiroDisponivel,
    action: string,
  ): boolean {
    return (
      !isHistoricalRevision(script) ||
      window.confirm(
        `Esta não é a revisão mais recente. Versão escolhida: v${script.versao}. Deseja ${action} mesmo assim?`,
      )
    );
  }

  async function approve(script: RoteiroDisponivel) {
    if (!confirmHistorical(script, "aprovar esta revisão")) return;
    setPreparing(true);
    setError(null);
    try {
      await flight.run(() => api.aprovarRevisao(script.id));
      await loadScripts(script.id);
      setReadiness(await api.obterReadinessRevisao(script.id));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Falha ao aprovar revisão",
      );
    } finally {
      setPreparing(false);
    }
  }

  async function bindMedia(script: RoteiroDisponivel) {
    if (!projectId) return;
    if (!confirmHistorical(script, "configurar mídia a partir desta revisão"))
      return;
    setPreparing(true);
    setError(null);
    try {
      const result = await flight.run(() =>
        api.vincularMidia(script.id, projectId, sessionIds),
      );
      await loadScripts(result.roteiro_id);
      setReadiness(await api.obterReadinessRevisao(result.roteiro_id));
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Falha ao configurar mídia",
      );
    } finally {
      setPreparing(false);
    }
  }

  async function prepareVisualQueries(script: RoteiroDisponivel) {
    setPreparing(true);
    setError(null);
    setSuccess(null);
    setVisualServiceUnavailable(false);
    try {
      const prepared = await visualFlight.run(script.id, {
        prepare: api.prepararProducao,
        list: api.listarRoteirosDisponiveis,
        readiness: api.obterReadinessRevisao,
      });
      setScripts(prepared.scripts);
      setSelectedId(prepared.selectedId);
      setReadiness(prepared.readiness);
      setSuccess(
        "VisualQueries geradas com sucesso. Nova revisão visual selecionada automaticamente.",
      );
    } catch (reason) {
      const failure = visualQueriesFailure(reason);
      setError(failure.message);
      setVisualServiceUnavailable(failure.serviceUnavailable);
    } finally {
      setPreparing(false);
    }
  }

  async function create(script: RoteiroDisponivel) {
    if (!confirmHistorical(script, "usar esta revisão")) return;
    setSubmitting(true);
    setError(null);
    try {
      const workflow = await flight.run(() => api.criarWorkflow(script.id));
      navigate(`/productions/${workflow.id}/overview`);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Falha ao iniciar produção",
      );
    } finally {
      setSubmitting(false);
    }
  }

  const report = readiness?.production_readiness ?? null;
  const mediaMissing = hasReadinessIssue(report, "media_scope_missing");
  const visualMissing = hasReadinessIssue(report, "visual_readiness_not_ready");
  const visualQueriesMissing = hasMissingVisualQueries(report);

  return (
    <div className="page narrow-page">
      <PageHeader
        title="Iniciar nova produção"
        description="Escolha a versão do roteiro, confira os requisitos e inicie a produção. A versão mais recente vem selecionada; você pode escolher outra."
      />
      {error && <Alert tone="danger">{error}</Alert>}
      {success && <Alert tone="success">{success}</Alert>}
      {loading ? (
        <LoadingState />
      ) : scripts.length === 0 ? (
        <EmptyState
          title="Nenhum roteiro disponível"
          detail="O Ideas Hub pode estar offline ou ainda não há revisões."
        />
      ) : (
        <>
          <Card title="1. Escolher revisão">
            <label htmlFor="production-revision">Roteiro e revisão</label>
            <select
              id="production-revision"
              value={selectedId}
              disabled={preparing || submitting}
              onChange={(event) => {
                setSelectedId(event.target.value);
                setSuccess(null);
                setVisualServiceUnavailable(false);
              }}
            >
              {scripts.map((script) => (
                <option key={script.id} value={script.id}>
                  {script.titulo} · v{script.versao} · {script.revision_kind} ·{" "}
                  {script.status}
                  {script.is_latest ? " · mais recente" : ""}
                </option>
              ))}
            </select>
            {selected && (
              <div className="detail-grid">
                <div>
                  <strong>Versão</strong>
                  <p>
                    v{selected.versao} · {selected.revision_kind}
                  </p>
                </div>
                <div>
                  <strong>Status</strong>
                  <p>
                    <StatusBadge value={selected.status} />
                  </p>
                </div>
                <div>
                  <strong>Criada em</strong>
                  <p>
                    {selected.created_at
                      ? new Date(selected.created_at).toLocaleString("pt-BR")
                      : "não disponível"}
                  </p>
                </div>
                <div>
                  <strong>Mídia vinculada</strong>
                  <p>{selected.media_bound ? "configurada" : "ausente"}</p>
                </div>
              </div>
            )}
            {selected && isHistoricalRevision(selected) && (
              <Alert tone="warning">
                Esta não é a revisão mais recente. Versão escolhida: v
                {selected.versao}. Versão mais recente: v{latestSelectedVersion}
                . Você pode usá-la mediante confirmação explícita.
              </Alert>
            )}
          </Card>

          {selected && (
            <Card title="2. Conferir os requisitos">
              {!readiness ? (
                <LoadingState label="Conferindo requisitos da produção…" />
              ) : (
                <>
                  <p>
                    Prontidão para produção:{" "}
                    <StatusBadge value={report?.status} />
                  </p>
                  {readiness.blockers.map((blocker) => (
                    <Alert
                      key={blocker.code}
                      tone={
                        blocker.code === "media_scope_missing"
                          ? "warning"
                          : "danger"
                      }
                    >
                      <strong>{blocker.code}</strong>: {blocker.message}
                      {blocker.details && blocker.details.length > 0 && (
                        <pre>{JSON.stringify(blocker.details, null, 2)}</pre>
                      )}
                    </Alert>
                  ))}

                  {selected.status === "rascunho" && (
                    <button
                      disabled={preparing}
                      onClick={() => void approve(selected)}
                    >
                      {preparing ? "Aprovando…" : "Aprovar esta revisão"}
                    </button>
                  )}

                  {selected.status === "aprovado" && mediaMissing && (
                    <div>
                      <h3>Configurar mídia</h3>
                      <p>
                        O roteiro está aprovado. Falta configurar o escopo de
                        mídia para esta produção.
                      </p>
                      <label htmlFor="media-project">Projeto de mídia</label>
                      <select
                        id="media-project"
                        value={projectId}
                        onChange={(event) => setProjectId(event.target.value)}
                      >
                        <option value="">
                          Selecione explicitamente um projeto
                        </option>
                        {projects.map((project) => (
                          <option key={project.id} value={project.id}>
                            {project.name}
                          </option>
                        ))}
                      </select>
                      {projectId && (
                        <fieldset>
                          <legend>Sessões permitidas</legend>
                          <p>
                            Sem sessões marcadas, todas as sessões do projeto
                            ficam no escopo.
                          </p>
                          {sessions.map((session) => (
                            <label
                              key={session.id}
                              style={{ display: "block" }}
                            >
                              <input
                                type="checkbox"
                                disabled={session.status !== "ready"}
                                checked={sessionIds.includes(session.id)}
                                onChange={(event) =>
                                  setSessionIds((current) =>
                                    event.target.checked
                                      ? [...current, session.id]
                                      : current.filter(
                                          (id) => id !== session.id,
                                        ),
                                  )
                                }
                              />{" "}
                              {session.label} · {session.status}
                            </label>
                          ))}
                        </fieldset>
                      )}
                      <button
                        disabled={preparing || !projectId}
                        onClick={() => void bindMedia(selected)}
                      >
                        {preparing ? "Configurando…" : "Configurar mídia"}
                      </button>
                    </div>
                  )}

                  {visualQueriesMissing && (
                    <VisualQueriesActionPanel
                      preparing={preparing}
                      serviceUnavailable={visualServiceUnavailable}
                      onGenerate={() => void prepareVisualQueries(selected)}
                    />
                  )}
                  {visualMissing && !visualQueriesMissing && (
                    <p>
                      A estrutura visual permanece bloqueada; corrija os issues
                      exibidos antes da produção.
                    </p>
                  )}
                </>
              )}
            </Card>
          )}

          {selected && (
            <Card title="3. Iniciar produção">
              <p>
                A produção preservará a versão escolhida do roteiro{" "}
                <code>{selected.id}</code>.
              </p>
              <button
                className="primary-button"
                disabled={
                  submitting ||
                  preparing ||
                  !canStartProduction(selected, report)
                }
                onClick={() => void create(selected)}
              >
                {submitting ? "Iniciando…" : "Iniciar produção"}
              </button>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
