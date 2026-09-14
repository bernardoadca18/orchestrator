# NeoVids Operator Console Foundation V1

## Scope and audit

The live Orchestrator repository was the source of truth. At the start of this pass it was clean on
`master` at `68dc3ae6496b614704c84307ac11a7f4abb84aac`. The adjacent repositories were inspected but
not changed: Video Composer `main@e3394ab`, Ideas Hub `main@98b74b5`, and Media Indexer
`master@b3d9c2f`. No applicable `AGENTS.md` exists inside Orchestrator; the adjacent repositories'
instructions were not applicable because they were not modified.

Baseline validation was:

- frontend: 16 tests passed and the Vite production build passed;
- backend: 512 tests passed, 24 integration tests skipped;
- worktrees: clean in all inspected repositories;
- migration head: `019`.

The initial frontend mixed workflow creation, list management, and attention heuristics on one
page. `WorkflowDetail.tsx` was 584 lines and polled workflow plus stable artifacts every three
seconds. Titles persisted in `roteiro_snapshot` were not used in the workflow list. Action
availability was derived from status strings in the browser. Stage 14–18 APIs already exposed rich
VisualPlan, ProductionTimeline, PresentationSpec, AudioMixPlan, render and QA data. The genuinely
missing contracts were a compact operator projection, server-owned capabilities/attention, durable
Human Review, a safe music catalog, and durable workflow music override.

Non-goals remain publishing, auth/user management, Stage 19, NLE behavior, visual source override,
Music V2, upload/import, SFX/gameplay audio, Composer changes, Media Indexer changes, Ideas Hub
changes, and cosmetic branding.

## Frontend architecture and routes

The frontend is feature-oriented under `src/features`, with shared functional components in
`src/components`, polling/data hooks in `src/hooks`, and server-state utilities in `src/data`.
The former `WorkflowDetail` module is now only a compatibility export; narration editing remains in
its own feature and keeps the existing chunk/take components.

Routes:

- `/dashboard`
- `/productions`
- `/productions/new`
- `/productions/:workflowId/overview`
- `/productions/:workflowId/narration`
- `/productions/:workflowId/visuals`
- `/productions/:workflowId/presentation`
- `/productions/:workflowId/program-audio`
- `/productions/:workflowId/render`
- `/productions/:workflowId/history`
- `/review`
- `/music`
- `/workflow/:id` redirects to the compatible overview deep link.

The app shell and workflow header stay mounted across workflow subroutes. Important production
filters are URL query parameters. The UI uses semantic navigation, native buttons/labels,
keyboard-focus styles, a native focus-managed dialog with ESC behavior, text status labels, fluid
players, and controlled table overflow.

## Operator API and data ownership

`operator_console_v1` is a projection, not another source of truth. It derives from
`WorkflowExecucao`, the compatible current Stage 13–18 artifacts, and Human Review rows. It never
persists artifact copies and never calls Ideas Hub for a workflow title.

Endpoints:

- `GET /operator/workflows`
  - supports `state`, `search`, `production_status`, `review_status`, `ordering`, `limit`, `offset`;
  - returns title/profile/duration/status/phase/error, attention reasons, capabilities, render/QA/
    review identities, and music override revision.
- `GET /operator/workflows/{id}` returns the hot summary plus cold artifact identities.
- `GET /operator/workflows/{id}/history` returns errors, render history, QA history, audio edit
  revisions, and Human Review history.
- `GET /operator/workflows/{id}/composer-inspection` derives PresentationSpec/AudioMixPlan in
  memory for inspection, strips private source references, and does not persist or launch work.
- `GET /operator/review-queue` returns intervention-only prioritized work.
- `GET /operator/music-assets` returns validated safe catalog metadata.
- `GET /operator/music-assets/{asset_id}/audio?sha256=...` previews only an exact registry-bound,
  integrity-verified asset.

No operator response exposes filesystem paths, credentials, tokens, or `source_reference`.

## Capabilities and attention

Capabilities are server-owned `{allowed, reason, invalidates}` values:

- `can_cancel`, `can_resume`, `can_retry`;
- `can_regenerate_narration`, `can_apply_audio_edits`;
- `can_run_standard_qa`, `can_run_deep_qa`;
- `can_review`, `can_change_music`.

The browser renders the decision and reason; mutating endpoints still revalidate state under a row
lock where required. Attention is a deterministic projection using stable codes including
`audio_failed`, `production_failed`, `production_blocked`, `music_unresolved`, `qa_failed`,
`qa_warning`, `human_review_pending`, `changes_requested`, `stale_review`, and
`service_degraded`. A completed workflow is not automatically an attention item.

## Human Review V1

Migration `020` creates append-only `workflow_human_reviews`. Each decision stores:

- workflow and render IDs;
- render fingerprint and output SHA-256;
- optional exact QA ID/fingerprint;
- `approved`, `changes_requested`, or `rejected`;
- note, optional local operator label, idempotency key, and timestamp.

`changes_requested` and `rejected` require a note. `approved` may have one. Human approval never
publishes content. The POST locks the workflow and compares the submitted identity with the
semantic current completed render; mismatch is HTTP 409. Reusing an idempotency key with another
decision is also a conflict.

Endpoints:

- `GET /workflow/{id}/human-review`
- `GET /workflow/{id}/human-review/history`
- `POST /workflow/{id}/human-review`

The current decision is the latest row matching all current render identity fields. An approval for
an older output remains historical and is displayed as `APPROVED HISTÓRICA — RENDER CORRENTE NÃO
REVISADO`; it never approves a new render. Review queue categories are
`final_video_review`, `qa_warning_review`, `changes_requested`, and `audio_attention`.

## Program Audio and music override

The Music Library supports client-side search plus mood/style/energy/rights filters over a bounded
validated registry. It exposes duration, format, measured LUFS/True Peak, owner, license,
monetization, attribution and controlled preview.

Migration `020` adds `music_override_asset_id`, `music_override_revision`, and the last idempotency
key to the workflow. The registry remains immutable catalog data. Revision zero retains the legacy
registry-JSON override compatibility path until the first explicit operator mutation; after that,
NULL means automatic deterministic selection.

Actions:

- `PUT /workflow/{id}/music-override`
- `DELETE /workflow/{id}/music-override`

Both require `Idempotency-Key` and `expected_revision`. They lock the workflow and reject lost
updates. Setting an override validates catalog identity, rights/vocals policy, file integrity,
timeline compatibility, and AudioMixPlan construction. A change sets the earliest required phase
to `render` and queues normal Stage 17 processing. It does not invalidate or regenerate narration,
transcription, NarrationAlignment, VisualPlan, or ProductionTimeline. Because AudioMixPlan identity
participates in the render request fingerprint, old render/QA/Human Review automatically become
historical.

## Polling and cache strategy

The compact operator snapshot is hot and uses the existing serial, cancelable, backoff polling.
Narration detail is warm and is fetched only on the narration route. Cold artifacts are loaded only
on their routes and cached by server-provided fingerprint/identity:

- VisualPlan and ProductionTimeline by fingerprint;
- Composer input by timeline/PresentationSpec/AudioMixPlan identities;
- completed render/QA and history by current identities.

Concurrent cold reads share one cache-owned promise. Stable identities do not refetch. Identity
changes or explicit action invalidation refresh only the named resources. Navigation aborts active
hot/warm polling; an immutable cold request may finish into the cache without updating an unmounted
component, which also makes React StrictMode remounts safe. There is no Redux/Zustand and
server-owned state is not copied into a client state machine.

## Future cosmetic handoff

A future visual pass can safely change CSS custom properties, typography, spacing, iconography,
component markup inside `components/ui.tsx`, responsive presentation, and presentation-only class
names. It should not need to change API contracts, routes, cache identity rules, capability logic,
database models, review semantics, or production state transitions.
