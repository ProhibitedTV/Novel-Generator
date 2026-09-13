# Long-form output reliability

Novel Generator treats a local model response as an intermediate artifact, not automatically as a trustworthy finished chapter or structured record. Long novels fail in ways that short prompts rarely expose: context-window crowding, output-token truncation, malformed JSON, stale continuity after rewrites, final prose drifting away from stored checkpoints, and unresolved story debt surviving into a superficially climactic ending.

This document describes the safeguards added around those failure modes.

## Context headroom and proactive output capacity

A prompt can fit inside a model's configured context window and still be operationally broken if it leaves too little room for the completion. This matters most for local chapter generation: a 32K context can be consumed by story bible, continuity, recall, arc state, horizon data, QA context, and current prose before a 2,000-3,000 word chapter has room to finish.

Novel Generator therefore makes local completion budgets explicit:

```env
OLLAMA_NUM_CTX=32768
OLLAMA_NUM_PREDICT=8192
OPENAI_COMPATIBLE_MAX_TOKENS=8192
NOVEL_CONTEXT_HEADROOM_ENABLED=1
NOVEL_CONTEXT_HEADROOM_RESERVE_TOKENS=8192
```

For Ollama, `OLLAMA_NUM_PREDICT` is sent as `options.num_predict`; the default is 8192 tokens. For OpenAI-compatible local servers, `OPENAI_COMPATIBLE_MAX_TOKENS` is sent as `max_tokens` by default. If a compatible server rejects that field with HTTP 400/422, Novel Generator retries without it rather than making an otherwise usable local backend fail.

The headroom preflight uses a provider's known configured context size and reserves completion capacity before the call. Today that is directly available for Ollama through `OLLAMA_NUM_CTX`. On smaller context windows, the reserve is capped at one third of the configured context so the prompt is not starved completely. An OpenAI-compatible completion budget is still enforced proactively, but headroom shedding is not guessed from an unknown server context size.

When a prompt exceeds the resulting input budget, the preflight removes only derived/read-only context blocks in this order:

1. whole-book quality-trend audit;
2. long-range chapter recall;
3. story-arc audit;
4. whole-book unresolved-arc audit; then
5. narrative horizon.

It never truncates the chapter prose, current draft, story bible, outline, durable continuity payload, or other core task data. The end-of-book story-debt audit is also protected from this shedding order so final QA keeps its closure evidence.

The attempt metadata records the estimated input size before/after headroom shaping, the input budget, requested reserve, whether headroom was satisfied, and which optional blocks were removed. No prompt/manuscript text is copied into metadata.

Headroom is preventive. If the immutable/core prompt itself still exceeds the configured model window, existing provider error handling remains authoritative rather than silently deleting canon.

## Prose output-limit recovery

Local providers can successfully return text while also reporting that generation stopped because the output budget was exhausted. Accepting that response as a complete chapter produces one of the most damaging long-form failures: prose simply ends mid-scene while the pipeline continues as if the chapter were finished.

Novel Generator now inspects the provider's reported stop reason after prose stages. When Ollama or an OpenAI-compatible backend explicitly reports a length/token-limit stop, the worker can run a bounded continuation recovery pass.

Recovery applies only to prose-producing stages:

- chapter draft;
- chapter revision;
- chapter expansion;
- developmental revision;
- publication humanization;
- publication compression; and
- final chapter editing.

Structured JSON stages continue to use the structured validation/repair path instead of prose continuation.

The continuation prompt does not resend the entire accumulated chapter. It keeps a bounded excerpt of the original task plus the tail of generated prose, asks for continuation prose only, and preserves POV, tense, canon, chapter outcome, and voice. When the model repeats the seam, overlap is removed before the continuation is appended. A response that appears to restart the chapter from its opening is rejected.

If the provider still reports a length stop after the configured continuation budget, the worker raises an explicit truncation error rather than silently accepting an incomplete chapter.

Configuration:

```env
NOVEL_TRUNCATION_RECOVERY_ENABLED=1
NOVEL_TRUNCATION_MAX_CONTINUATIONS=2
NOVEL_TRUNCATION_CONTEXT_MAX_CHARS=18000
```

`NOVEL_TRUNCATION_MAX_CONTINUATIONS` is clamped to 0-4. The continuation context budget is clamped to 8,000-40,000 characters.

Each continuation is a normal supervised provider attempt, so it receives the same attempt logging, prompt-size telemetry, provider token metrics, stop reason, headroom shaping where the context size is known, and failure handling as an ordinary generation call.

## Schema-constrained structured output

The original structured-output path asked models to produce JSON and then parsed/validated it. That remains the fallback, but providers that support native schema controls can now receive the actual Pydantic contract for the stage.

Stage schemas currently include:

- `StoryBible` for story-bible generation;
- `list[StructuredOutlineEntry]` for full/chunked outlines;
- `ChapterPlan` for chapter planning;
- `ChapterCritique` for chapter critique;
- `ChapterContinuityUpdate` for continuity checkpoints;
- `ManuscriptQaReport` for manuscript QA/publication readiness; and
- `DevelopmentalRewritePlan` for developmental rewrite planning.

The schema travels through the existing pipeline using a private runtime marker that is stripped before provider messages are sent. The model never sees the marker itself.

### Ollama

When a stage schema is available, the Ollama client sends the JSON Schema directly through the native `format` field and keeps the configured low structured temperature. JSON-only calls without a mapped stage schema continue to use ordinary JSON mode. The configured `num_ctx` and `num_predict` options remain present for both prose and structured calls.

### OpenAI-compatible local servers

When a stage schema is available, the client requests `response_format.type = json_schema`. Local servers vary in how fully they implement the OpenAI-compatible surface, so the request uses a compatibility ladder. For a schema-constrained call it tries, in order:

1. JSON Schema + configured `max_tokens`;
2. JSON-object mode + configured `max_tokens`;
3. ordinary chat + configured `max_tokens`; then
4. ordinary chat without `max_tokens` if the local server rejects that field too.

For JSON-only calls without a stage schema, the same ladder starts at JSON-object mode. HTTP 400/422 moves to the next compatibility candidate; transport failures and other HTTP errors continue through the normal retry/error path.

The existing Pydantic validation and JSON-repair pass remain in place after provider generation. Native structured output reduces malformed responses; it does not replace application validation.

## Provider stop/usage telemetry

Prompt-size telemetry is recorded before a call. Providers can additionally report actual token and stop information after a call.

Ollama telemetry can include prompt/eval counts, load/prompt/eval/total durations, prompt/completion throughput, `done_reason`, and actual context-window utilization when `num_ctx` is known.

OpenAI-compatible telemetry can include prompt/completion/total tokens, cached prompt tokens, reasoning tokens, `finish_reason`, and the response model identifier.

These fields are persisted under the successful stage attempt's `metadata.provider_metrics`. Prompt text and manuscript output are not copied into telemetry.

The truncation-recovery layer consumes these stop reasons directly, which turns observability into a generation safety feature rather than passive logging. Headroom metadata complements this by showing when optional context was removed before a call to preserve completion capacity.

## Final manuscript QA sees final prose

A whole-book QA pass based only on chapter summaries and old QA metadata can miss regressions introduced by later developmental, humanization, compression, or line-edit passes. The bounded manuscript-QA representation therefore retains literal final prose evidence for every chapter.

Depending on context pressure, each chapter keeps bounded opening and/or closing excerpts. Detail degrades uniformly before chapter coverage is sacrificed. Even the extreme fallback preserves a small final-prose closing window for every chapter.

This lets final QA inspect evidence for problems such as:

- an abrupt or truncated chapter ending;
- repeated final beats or generated-feeling phrasing introduced during editing;
- prose that no longer agrees with a saved continuity checkpoint; and
- a final scene that sounds conclusive while failing to deliver the stored story turn.

The complete manuscript remains on disk/database as the source of truth; the QA capsule is only a bounded read view.

## End-of-book story-debt audit

Late-book convergence rules reduce the creation of new story debt, but the finished manuscript also needs an explicit check that existing debt was actually paid.

The deterministic ending-debt audit reads the final live continuity state and the story bible's ending promise. It reports remaining:

- open threads;
- still-live promises;
- trust fractures;
- emotional loops;
- memory damage; and
- civilian pressure points.

It also ranks live open threads/promises that strongly overlap the story-bible ending promise and includes the final chapter's saved outcome/state-after/permanent-consequence metadata.

Remaining live state is not automatically treated as a defect. Grief, political fallout, damaged relationships, lasting injuries, and sequel-facing questions can intentionally survive the final page. The editorial requirement is narrower: final QA must distinguish intentional residue from accidentally abandoned central obligations, and a sequel hook cannot substitute for closure of the current novel's primary conflict/emotional contract.

The audit is injected into manuscript QA and is also persisted deterministically into the resulting QA report. If live debt strongly overlaps the stated ending promise, the QA report receives an explicit warning even if the model otherwise returns a reassuring verdict.

## Final-edit integrity and publication-readiness guard

Final line editing is intended to polish rather than structurally rewrite a chapter. A local model can still occasionally compress or expand far beyond that mandate. The publication guard snapshots each chapter before the final edit and deterministically rolls back only catastrophic length regressions, including large shrink/balloon ratios or a previously compliant chapter being pushed materially outside the configured range.

Rollback events store counts/reasons only; they do not copy chapter prose into event metadata. A normal line-edit delta is accepted unchanged.

The final publication-readiness label also has a deterministic veto layer. It does not fail/export-block the run; it prevents a manuscript from being labeled `publication-ready` when book-scale requirements are visibly unmet. Current blockers include:

- final manuscript word count below 90% of the configured target;
- final manuscript word count above 115% of target;
- severe per-chapter length outliers; and
- live story-debt candidates that overlap the ending promise without final QA explicitly classifying them as paid off, intentional aftermath, or deliberate sequel residue.

If any blocker exists, the readiness score is capped below the configured publication threshold, the label becomes `needs editorial revision`, the blockers are added to QA warnings, and a `publication_readiness_guard_applied` event is recorded.

This distinction is deliberate: an imperfect manuscript should still export for human review, but the system should not falsely certify it as publication-ready.

## Continuity reconciliation after structural revision

Developmental revision is allowed to sharpen or materially alter a chapter's structural consequence. That means the chapter summary and continuity checkpoint created during the original draft can become stale.

After developmental revision waves, Novel Generator now identifies chapters whose latest `developmental_chapter_revision_completed` event is newer than their latest `developmental_continuity_reconciled` event.

Only those structurally changed chapters receive new inference calls:

1. regenerate the chapter summary from the revised prose;
2. regenerate the structured continuity update against the ledger state that precedes that chapter; and
3. replay the book's continuity checkpoints in chapter order to rebuild the durable final ledger.

Unchanged chapters reuse their saved checkpoints. This keeps the reconciliation cost proportional to the number of chapters actually structurally rewritten rather than doubling inference across a 32- or 64-chapter manuscript.

The replay passes through the same live-vs-historical continuity lifecycle logic used during normal drafting, so healed/resolved live state can remain resolved instead of being resurrected as append-only ghost debt.

If summary refresh fails, the saved summary is kept and an explicit fallback event is recorded. If continuity refresh fails but a saved checkpoint exists, the old checkpoint is replayed and a fallback event is recorded. Final manuscript QA still has literal final prose plus ending-debt evidence, providing another layer of detection when reconciliation cannot fully refresh a chapter.

## Runtime ordering

The worker installs the long-form runtime transforms in an intentional order:

1. stage-specific structured schemas;
2. bounded context / prompt telemetry;
3. long-form arc and manuscript-QA shaping;
4. long-range chapter recall;
5. adaptive length enforcement;
6. live continuity lifecycle semantics;
7. post-developmental continuity reconciliation;
8. final-edit/publication-readiness guards;
9. context headroom preflight; and
10. prose truncation recovery.

Schema shaping is installed before telemetry so the private schema marker is not counted as model prompt text. Reconciliation is installed after continuity lifecycle handling so ledger replay respects live snapshot semantics. Headroom wraps the supervised call outside telemetry, so prompt-size telemetry measures the actual post-shedding messages sent to providers with known context sizes. Truncation recovery is installed last so continuation attempts flow through the same headroom + telemetry stack as ordinary prose calls.

## Failure philosophy

The reliability layers follow three principles:

- **Do not silently accept incomplete output.** Explicit provider length stops on prose are recovered or surfaced as failures.
- **Do not confuse optimization with canon.** Bounded context, headroom shaping, schema markers, audits, and retrieval views never replace the durable manuscript/outline/ledger.
- **Fail open where a safeguard is advisory, fail closed where silent corruption is worse.** Telemetry extraction, audit injection, context shedding, provider completion-budget hints, and schema optimizations can fall back safely. Persistently truncated prose cannot be treated as complete, and a deterministic publication blocker cannot be overridden by a superficially high model score.

The result is still local-first: none of these safeguards requires embeddings, a hosted memory service, a cloud model, or a separate inference provider.