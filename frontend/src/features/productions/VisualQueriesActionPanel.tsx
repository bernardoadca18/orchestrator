interface VisualQueriesActionPanelProps {
  preparing: boolean;
  serviceUnavailable: boolean;
  onGenerate: () => void;
}

export function VisualQueriesActionPanel({
  preparing,
  serviceUnavailable,
  onGenerate,
}: VisualQueriesActionPanelProps) {
  return <section aria-labelledby="visual-queries-action-title">
    <h3 id="visual-queries-action-title">Estrutura visual não preparada</h3>
    {serviceUnavailable ? (
      <p role="alert">O serviço responsável pelo Visual Enrichment está indisponível.</p>
    ) : <>
      <p>Este roteiro ainda não possui VisualQueries. A narração não será modificada.</p>
      <p>O NeoVids analisará a narração apenas para gerar metadados de busca visual. O texto narrado permanecerá inalterado.</p>
      <button disabled={preparing} onClick={onGenerate}>
        {preparing ? "Gerando VisualQueries…" : "Gerar VisualQueries"}
      </button>
    </>}
  </section>;
}
