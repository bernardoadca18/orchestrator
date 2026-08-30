import assert from "node:assert/strict";
import { test } from "node:test";

import { presentContentQuality } from "./contentQuality";

test("pronunciation disagreement is presented as accepted warning without retry", () => {
  const result = presentContentQuality({
    status: "passed",
    classification: "pronunciation_or_asr_disagreement",
    retry_required: false,
    metrics: {
      normalized_wer: 0.2,
      structural_coverage_ratio: 1,
      pronunciation_or_asr_mismatches: [
        { expected: "veículos", observed: "vaículos", occurrences: 2 },
      ],
    },
  });

  assert.equal(result?.outcomeLabel, "Aceito com aviso");
  assert.equal(result?.tone, "warning");
  assert.equal(result?.retryRequired, false);
  assert.deepEqual(result?.mismatches[0], {
    expected: "veículos",
    observed: "vaículos",
    occurrences: 2,
  });
});

test("probable content loss is presented as blocking and retryable", () => {
  const result = presentContentQuality({
    status: "rejected",
    classification: "probable_content_loss",
    retry_required: true,
    retry_reason: "probable_content_loss",
    metrics: { longest_missing_contiguous_token_count: 9 },
  });

  assert.equal(result?.outcomeLabel, "Rejeitado");
  assert.equal(result?.tone, "error");
  assert.equal(result?.retryRequired, true);
  assert.match(result?.retryLabel ?? "", /probable_content_loss/);
});
