# Long-form output reliability

Novel Generator treats a local model response as an intermediate artifact, not automatically as a trustworthy finished chapter or structured record. Long novels fail in ways that short prompts rarely expose: context-window crowding, output-token truncation, malformed JSON, stale continuity after rewrites, final prose drifting away from stored checkpoints, and unresolved story debt surviving into a superficially climactic ending.

This document describes the safeguards around those failure modes.

## Context headroom and proactive output capacity

A prompt can fit inside a model's configured context window and still be operationally broken if it leaves too little room for the completion. This matters most for local chapter generation: story bible, continuity, recall, arc state, horizon data, QA context, and current prose can consume the window before a 2,000-3,000 word chapter has room to finish.

Novel Generator makes both provider ceilings and application-side output planning explicit:

```env
OLLAMA_NUM_CTX=32768
OLLAMA_NUM_PREDICT=8192
OPENAI_COMPATIBLE_CONTEXT_TOKENS=0
OPENAI_COMPATIBLE_MAX_TOKENS=8192
NOVEL_CONTEXT_HEADROOM_ENABLED=1
NOVEL_CONTEXT_HEADROOM_RESERVE_TOKENS=8192
NOVEL_STAGE_OUTPUT_BUDGETS_ENABLED=1
```

For Ollama, `OLLAMA_NUM_PREDICT` is sent as `options.num_predict`. For OpenAI-compatible local servers, `OPENAI_COMPATIBLE_MAX_TOKENS` is sent as `max_tokens` when supported.

`OPENAI_COMPATIBLE_CONTEXT_TOKENS` is an application-side context-window hint. Its default is `0`, meaning unknown. If the loaded LM Studio/vLLM/OpenAI-compatible model's real window is known, set this value and Novel Generator can apply the same headroom accounting used for Ollama. The value is never serialized into `/chat/completions`.

### Stage-aware output budgets

A single 8K completion cap is wasteful for a small chapter plan but can be appropriate for a prose chapter. When stage budgets are enabled, Novel Generator derives a per-call ceiling from the configured provider maximum:

- prose-producing stages: up to 8,192 tokens;
- large structured stages (`outline`, `outline_chunk`, `manuscript_qa`, `publication_readiness`, `developmental_rewrite`): up to 6,144;
- story bible: up to 4,096;
- chapter plan, critique, and continuity update: up to 3,072;
- chapter summary: up to 2,048;
- other stages: up to 4,096.

These values are ceilings, not promises. A stage hint can lower `num_predict`/`max_tokens`, but can never raise the provider-wide configured maximum. If a small context window forces a smaller safe reserve, the provider output budget is lowered to the same value.

The stage budget travels through the existing message-only provider boundary as an application-private control marker. Ollama and OpenAI-compatible clients strip that marker before making the upstream request; it never appears as a model chat role.

### Independent controls

Prompt shedding and provider output caps are intentionally independent:

- `NOVEL_CONTEXT_HEADROOM_ENABLED=0` disables optional-context shedding but keeps stage-aware provider output limits when `NOVEL_STAGE_OUTPUT_BUDGETS_ENABLED=1`.
- `NOVEL_STAGE_OUTPUT_BUDGETS_ENABLED=0` disables per-stage provider caps while headroom can remain active. In that mode headroom reserves against the configured provider-wide completion ceiling instead of pretending the provider was capped lower.
- disabling both bypasses this wrapper entirely.

This separation is useful for debugging: an operator can inspect full prompts without also changing provider completion behavior.

### Headroom shedding

When the provider context size is known, the preflight reserves completion capacity before the call. On small windows the reserve is capped so the prompt is not starved completely.

If the prompt exceeds the resulting input budget, only derived/read-only blocks are removed, in this order:

1. whole-book quality-trend audit;
2. long-range chapter recall;
3. story-arc audit;
4. whole-book unresolved-arc audit;
5. narrative horizon.

It never truncates current chapter prose, the story bible, outline, durable continuity payload, or other core task data. The end-of-book story-debt audit is also protected so final QA keeps its closure evidence.

Attempt metadata records size estimates, requested/effective reserve, whether headroom was satisfied, stage, enabled controls, final provider output budget, and which optional blocks were removed. Manuscript text is not copied into metadata. Actual provider-reported prompt token counts remain the authoritative measurement when available.

Headroom is preventive. If immutable/core prompt data itself still exceeds the model window, provider error handling remains authoritative rather than silently deleting canon.

## Prose output-limit recovery

Local providers can successfully return text while also reporting that generation stopped because the output budget was exhausted. Accepting that response as a complete chapter produces a damaging long-form failure: prose ends mid-scene while the pipeline continues as if the chapter were finished.

Novel Generator inspects provider stop telemetry after prose stages. When Ollama or an OpenAI-compatible backend explicitly reports a length/token-limit stop, the worker can run bounded continuation recovery.

Recovery applies to prose-producing stages such as chapter draft/revision/expansion, developmental revision, publication humanization/compression, and final chapter editing. Structured JSON stages continue through structured validation/repair instead of prose continuation.

The continuation prompt does not resend the entire accumulated chapter. It keeps bounded task guidance plus the tail of generated prose and asks for continuation prose only. Repeated seams are de-duplicated; a response that appears to restart the chapter from its opening is rejected.

If the provider still reports a length stop after the configured continuation budget, the worker raises an explicit truncation error rather than silently accepting incomplete prose.

```env
NOVEL_TRUNCATION_RECOVERY_ENABLED=1
NOVEL_TRUNCATION_MAX_CONTINUATIONS=2
NOVEL_TRUNCATION_CONTEXT_MAX_CHARS=18000
```

Each continuation is a normal supervised provider attempt, so it passes through the same output-budget, headroom, telemetry, stop-reason, and error-handling layers as an ordinary call.

## Schema-constrained structured output

Structured stages can carry their actual Pydantic contract to providers that support native schema controls. Schemas include `StoryBible`, outline entries, `ChapterPlan`, `ChapterCritique`, `ChapterContinuityUpdate`, `ManuscriptQaReport`, and `DevelopmentalRewritePlan`.

The schema uses a separate application-private marker that is stripped before provider chat messages are sent. Schema and output-budget controls can coexist on the same call without either marker reaching the model.

### Ollama

When a stage schema is available, the client sends it through native `format` and keeps the low structured temperature. JSON-only calls without a mapped schema use ordinary JSON mode. `num_ctx` and the effective stage-specific `num_predict` remain independent options.

### OpenAI-compatible local servers

Local servers vary in support for `response_format` and `max_tokens`, so these capabilities degrade independently rather than as one bundle. A schema-constrained request can try:

1. JSON Schema + `max_tokens`;
2. JSON Schema without `max_tokens`;
3. JSON-object mode + `max_tokens`;
4. JSON-object mode without `max_tokens`;
5. ordinary chat + `max_tokens`;
6. ordinary chat without `max_tokens`.

HTTP 400/422 advances through compatible candidates. Transport errors and other HTTP failures retain ordinary retry/error behavior. This means a server that supports schemas but rejects `max_tokens` keeps schema enforcement, while a server that rejects JSON Schema but accepts JSON-object mode keeps the completion budget.

Application-side Pydantic validation and JSON repair remain authoritative after generation. Native structured output reduces malformed responses; it never replaces validation.

## Provider stop/usage telemetry

Prompt-size telemetry is recorded before a call. Providers can additionally report actual token and stop information after a call.

Ollama telemetry can include prompt/eval counts, load/prompt/eval/total durations, throughput, `done_reason`, and actual context-window utilization when `num_ctx` is known.

OpenAI-compatible telemetry can include prompt/completion/total tokens, cached prompt tokens, reasoning tokens, `finish_reason`, response model, and context utilization when `OPENAI_COMPATIBLE_CONTEXT_TOKENS` is explicitly configured.

These fields are stored under the successful stage attempt's provider metrics. Prompt text and manuscript output are not copied into telemetry. Truncation recovery consumes stop reasons directly, turning observability into an active generation safeguard.

## Final manuscript QA sees final prose

Whole-book QA based only on summaries and old QA metadata can miss regressions introduced by later developmental, humanization, compression, or line-edit passes. The bounded manuscript-QA representation therefore retains literal final prose evidence for every chapter.

Depending on context pressure, each chapter keeps bounded opening and/or closing excerpts. Detail degrades uniformly before chapter coverage is sacrificed; even the extreme fallback retains a small final closing window for every chapter.

Final QA runs after the final-edit integrity guard. If a bad final edit is rolled back, QA sees the restored accepted prose rather than the rejected edit.

This exposes problems such as abrupt endings, repeated generated-feeling beats, prose/checkpoint drift, or a final scene that sounds conclusive without delivering the stored story turn.

## End-of-book story-debt audit

Late-book convergence reduces new story debt, but the finished manuscript must also prove that central existing debt was handled.

The deterministic ending-debt audit reads final live continuity state and the story bible's ending promise. It reports remaining open threads, promises, trust fractures, emotional loops, memory damage, and civilian pressure, and ranks open thread/promise candidates that overlap the ending promise.

Remaining live state is not automatically a defect. Grief, political fallout, damaged relationships, permanent injuries, and sequel-facing questions can survive the final page. The rule is narrower: the current novel's central conflict and emotional contract cannot be abandoned behind a sequel hook.

Publication-readiness classification is candidate-specific. A QA note saying one trust fracture is intentional aftermath does not clear a distinct unresolved public-exposure promise. Each central debt candidate must have enough lexical identity with an explicit positive QA classification such as resolved on page, paid off on page, intentional aftermath, or deliberate sequel residue.

## Final-edit integrity and publication-readiness guard

The final line edit is intended to polish, not structurally replace a chapter. The guard snapshots accepted prose before editing and compares the edit against that source.

It rolls back deterministic regressions such as:

- empty or whitespace-only final prose;
- catastrophic shrink/balloon ratios;
- a previously compliant chapter being pushed materially outside configured length bounds;
- newly introduced meta/outlining language;
- a newly introduced abstract future-summary ending pattern;
- newly inserted chapter headings; or
- newly inserted Markdown fences.

These checks are differential: a pre-existing pattern is not blamed on the final editor merely because it remains present. Actual prose word counts are used as the source of truth, so stale `word_count` metadata cannot hide a destructive edit.

Rollback events store counts and reasons, not chapter prose.

The final publication-readiness label then receives deterministic vetoes in addition to model/editorial scores. A manuscript cannot be labeled `publication-ready` when it is materially under/over its whole-book target, contains severe chapter-length outliers, or still carries candidate-specific central ending debt without explicit classification.

These blockers do not prevent export. They convert readiness to `needs editorial revision`, cap the readiness score below threshold, persist warnings, and keep the manuscript available for human review.

## Continuity reconciliation after structural revision

Developmental revision can materially alter a chapter's consequence, making the original summary and continuity checkpoint stale.

After developmental revision waves, only chapters whose latest structural revision is newer than their latest reconciliation receive new inference:

1. regenerate the chapter summary from revised prose;
2. regenerate structured continuity against the ledger state preceding that chapter;
3. replay chapter checkpoints in book order to rebuild the durable final ledger.

Unchanged chapters reuse saved checkpoints, keeping cost proportional to actual structural rewrites. Replay passes through live-vs-historical continuity lifecycle semantics, so resolved state is not resurrected as append-only ghost debt.

If summary refresh fails, the saved summary remains with a fallback event. If continuity refresh fails but a saved checkpoint exists, that checkpoint is replayed. Final QA still has literal final prose and ending-debt evidence as an additional detection layer.

## Runtime ordering

The worker installs long-form transforms in an intentional order:

1. stage-specific structured schemas;
2. bounded continuity/context shaping and prompt telemetry;
3. long-form arc and manuscript-QA shaping;
4. long-range chapter recall;
5. adaptive length enforcement;
6. live continuity lifecycle semantics;
7. post-developmental continuity reconciliation;
8. final-edit/publication-readiness guards;
9. independent context-headroom + stage-output-budget controls;
10. prose truncation recovery.

Schema shaping is installed before telemetry. Reconciliation follows lifecycle handling so ledger replay respects live snapshot semantics. Headroom/output-budget controls wrap the supervised call outside the telemetry layer. Truncation recovery is installed last so continuation attempts traverse the same provider-control and telemetry stack as normal prose calls.

## Failure philosophy

The reliability layers follow three principles:

- **Do not silently accept incomplete output.** Explicit length stops are recovered or surfaced.
- **Do not confuse optimization with canon.** Bounded context, private controls, audits, and retrieval views never replace durable manuscript/outline/ledger state.
- **Fail open where advisory optimization can safely degrade; fail closed where silent corruption is worse.** Telemetry extraction, optional-context shedding, provider control hints, and schema optimizations can fall back. Persistently truncated prose cannot be called complete, destructive final edits are rejected, and deterministic publication blockers cannot be overridden by a superficially high model score.

The result remains local-first: these safeguards require no embedding service, hosted memory layer, cloud model, or separate inference provider.
