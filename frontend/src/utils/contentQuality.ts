export type QualityTone = "ok" | "warning" | "error" | "unknown";

export interface ContentQualityPresentation {
  classification: string;
  classificationLabel: string;
  outcomeLabel: string;
  tone: QualityTone;
  retryRequired: boolean;
  retryLabel: string;
  metrics: Array<[string, string]>;
  mismatches: Array<{ expected: string; observed: string; occurrences: number }>;
  chunks: Array<{
    index: number;
    classification: string;
    status: string;
    wer: string;
    structuralCoverage: string;
  }>;
  asrTranscript: string | null;
}

const LABELS: Record<string, string> = {
  content_complete: "Conteúdo completo",
  pronunciation_or_asr_disagreement: "Divergência local de pronúncia/ASR",
  content_inconclusive: "Validação ASR inconclusiva",
  probable_content_loss: "Provável perda de conteúdo",
  premature_end: "Final prematuro",
  probable_repetition: "Provável repetição anormal",
  probable_reordering: "Provável reordenação",
  legacy_content_rejection: "Rejeição do quality guard legado",
};

const asObject = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;

const numberValue = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

const percentage = (value: unknown): string => {
  const numeric = numberValue(value);
  return numeric === null ? "—" : `${(numeric * 100).toFixed(1)}%`;
};

export function presentContentQuality(
  report: Record<string, unknown> | null,
): ContentQualityPresentation | null {
  if (!report) return null;
  const status = typeof report.status === "string" ? report.status : "unavailable";
  const classification = typeof report.classification === "string"
    ? report.classification
    : status === "rejected"
      ? "legacy_content_rejection"
      : "content_inconclusive";
  const retryRequired = report.retry_required === true;
  const tone: QualityTone = retryRequired || status === "rejected"
    ? "error"
    : classification === "content_complete"
      ? "ok"
      : "warning";
  const outcomeLabel = tone === "error"
    ? "Rejeitado"
    : tone === "warning"
      ? "Aceito com aviso"
      : "Aceito";
  const metrics = asObject(report.metrics) ?? {};
  const rawMismatches = Array.isArray(metrics.pronunciation_or_asr_mismatches)
    ? metrics.pronunciation_or_asr_mismatches
    : [];
  const mismatches = rawMismatches.flatMap((item) => {
    const mismatch = asObject(item);
    if (!mismatch || typeof mismatch.expected !== "string" || typeof mismatch.observed !== "string") {
      return [];
    }
    return [{
      expected: mismatch.expected,
      observed: mismatch.observed,
      occurrences: numberValue(mismatch.occurrences) ?? 1,
    }];
  });
  const chunkDiagnostics = asObject(report.chunk_diagnostics);
  const rawChunks = chunkDiagnostics && Array.isArray(chunkDiagnostics.chunks)
    ? chunkDiagnostics.chunks
    : [];
  const chunks = rawChunks.flatMap((item) => {
    const chunk = asObject(item);
    const index = chunk ? numberValue(chunk.chunk_index) : null;
    if (!chunk || index === null) return [];
    return [{
      index,
      classification: typeof chunk.classification === "string"
        ? LABELS[chunk.classification] ?? chunk.classification
        : "Indisponível",
      status: typeof chunk.status === "string" ? chunk.status : "unavailable",
      wer: percentage(chunk.normalized_wer),
      structuralCoverage: percentage(chunk.structural_coverage_ratio),
    }];
  });
  const asr = asObject(report.asr);
  return {
    classification,
    classificationLabel: LABELS[classification] ?? classification,
    outcomeLabel,
    tone,
    retryRequired,
    retryLabel: retryRequired
      ? `Sim — ${typeof report.retry_reason === "string" ? report.retry_reason : classification}`
      : "Não — divergências locais não disparam nova geração",
    metrics: [
      ["WER", percentage(metrics.normalized_wer)],
      ["CER", percentage(metrics.normalized_cer)],
      ["Cobertura lexical", percentage(metrics.coverage_ratio)],
      ["Cobertura estrutural", percentage(metrics.structural_coverage_ratio)],
      ["Deleções", percentage(metrics.deletion_ratio)],
      ["Substituições", percentage(metrics.substitution_ratio)],
      [
        "Maior span ausente",
        numberValue(metrics.longest_missing_contiguous_token_count)?.toString() ?? "—",
      ],
    ],
    mismatches,
    chunks,
    asrTranscript: asr && typeof asr.transcript === "string" ? asr.transcript : null,
  };
}

export function qualityToneColor(tone: QualityTone): string {
  if (tone === "error") return "#b91c1c";
  if (tone === "warning") return "#a16207";
  if (tone === "ok") return "#15803d";
  return "#475569";
}
