# Visual retrieval evaluation harness

`scripts/evaluate_visual_retrieval.py` is a read-only measurement tool for Media Indexer retrieval
and the current Visual Director output. It never ingests media, rebuilds indexes, generates audio,
creates a VisualPlan, or changes selection policy.

## Evaluation levels and inputs

- Dataset mode executes temporal Media Search and optional static Image Search using a versioned
  JSON/JSONL dataset. Stable project names and canonical session labels are resolved at runtime.
- Roteiro mode reads the immutable Ideas Hub script, extracts its VisualQueries and media scope,
  and executes the same raw retrieval calls. It does not require audio.
- Workflow mode reads the current compatible VisualPlan with `GET /workflow/{id}/visual-plan`,
  re-runs raw retrieval for inspection, and reports the plan's exact selected source and
  alternatives. It does not replay or approximate Visual Director ranking.

The bundled `data/gta-visual-retrieval/probes-v1.json` covers GTA IV, GTA V, GTA VI, mixed scope,
an out-of-scope preferred-game probe, project-wide scope, temporal/static-friendly prompts, and an
intentionally ambiguous prompt. Production VisualQueries should be evaluated with roteiro or
workflow mode rather than copied into the controlled dataset.

## Scope semantics

Temporal classification is objective. Canonical session labels beginning with `gta_iv_`, `gta_v_`,
or `gta_vi_` are the game authority. A non-empty resolved session set is
`EXPLICIT_ALLOWED_SESSIONS`; any returned, selected, or alternative temporal session outside it is
a `MEDIA_SCOPE_LEAKAGE` blocker. Null, absent, or empty `media_session_ids` means project-wide in
the current contract and deliberately does not promise game isolation.

Static images have no MediaSession. Image Search is project-scoped and its public contract supports
canonical provenance filters including `game`. The controlled dataset can exercise that filter.
The current `ScriptMediaContext.static_images`, however, exposes asset class, collection, source
owner, and source class, but not `game`. Therefore workflow/roteiro static evaluation must not infer
game isolation from temporal session scope. Static candidates without sufficient canonical game
provenance are `UNKNOWN_GAME`, never guessed.

## Run and review

From `orchestrator/backend`:

```bash
PYTHONPATH=. .venv/bin/python scripts/evaluate_visual_retrieval.py run \
  --dataset ../data/gta-visual-retrieval/probes-v1.json \
  --output-dir /tmp/gta-retrieval-probes-v1

PYTHONPATH=. .venv/bin/python scripts/evaluate_visual_retrieval.py run \
  --roteiro-id '<approved-script-uuid>' \
  --output-dir /tmp/gta-script-retrieval

PYTHONPATH=. .venv/bin/python scripts/evaluate_visual_retrieval.py run \
  --workflow-id '<workflow-uuid>' \
  --output-dir /tmp/gta-workflow-retrieval
```

Each run writes:

- `report.json`: complete, schema-versioned machine-readable evidence;
- `report.md`: bounded human-readable candidates and diagnostics;
- `review.csv`: editable manifest whose human label fields are initially blank.

Open `report.md` and `review.csv`. In workflow mode, fill `selected_label` and optional alternative
labels. In every mode, the first three raw temporal and static candidates have independent label
columns. `query_label` records `QUERY_BAD` or `QUERY_AMBIGUOUS` separately. Accepted labels are
`GOOD`, `ACCEPTABLE`, `BAD`, `WRONG_GAME`, `WRONG_CONTEXT`, `UNRESOLVED`, `QUERY_BAD`, and
`QUERY_AMBIGUOUS`. Then aggregate:

```bash
PYTHONPATH=. .venv/bin/python scripts/evaluate_visual_retrieval.py summary \
  --report /tmp/gta-workflow-retrieval/report.json \
  --labels /tmp/gta-workflow-retrieval/review.csv \
  --output /tmp/gta-workflow-retrieval/summary.json
```

The parser binds labels to both report and dataset identities, so labels cannot silently migrate
between runs. It reports rates by intent, selected source type, and canonical game, plus labeled
selected-vs-alternative comparisons. It does not call an LLM or claim precision/recall without
explicit relevance ground truth.

Compare two reports only when they share the exact dataset identity:

```bash
PYTHONPATH=. .venv/bin/python scripts/evaluate_visual_retrieval.py compare \
  --baseline /tmp/baseline/report.json \
  --candidate /tmp/candidate/report.json \
  --output /tmp/comparison.json
```

The report records current strategy names, thresholds, top-k values, fusion weights, project and
session discovery, index/search provenance returned by the APIs, latencies, and execution time.
These values are evidence identities only; the harness offers no tuning flags.
