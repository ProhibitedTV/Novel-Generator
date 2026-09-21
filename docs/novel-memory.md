# Bounded novel memory

Long novels create a different context problem than short-form generation: the durable continuity ledger grows chapter after chapter, while local models still have a finite useful attention budget. Sending the entire accumulated ledger into every chapter-level prompt eventually spends more context on old resolved history than on the facts that matter for the current scene.

Novel Generator keeps the full continuity ledger as the canonical stored state. The worker compiles read-only context views for chapter planning, drafting, critique, revision, expansion, developmental revision, publication polishing, and final editing. These views never replace persisted canon.

The continuity-memory compiler prioritizes current world state, chapter-relevant character state, open promises and threads, trust fractures, emotional loops, entity and system state, recent timeline events, system transitions, side-character decisions, civilian pressure, and a small amount of resolved history. Selection is deterministic: lexical relevance to the current chapter plan/outline and recent context is combined with recency. The canonical ledger is never truncated or mutated.

The continuity-update stage intentionally still receives the complete ledger. This preserves the cumulative state machine and prevents a compact prompt view from accidentally becoming the persisted source of truth.

## Causal narrative horizon

A rolling summary tells the model what just happened, but that alone does not protect long-range causality. Local models can still softly reset the story, accidentally resolve a later reveal too early, reuse the same obstacle shape, or arrive at the next planned chapter without having established the conditions that chapter needs.

For chapter planning, drafting, critique, revision, and expansion, the worker derives a small narrative-horizon packet from already persisted run state. It contains:

- the previous completed chapter's irreversible change, protagonist choice, permanent consequence, and resulting state;
- the previous outline ending state and concrete hook;
- a configurable lookahead over the next planned chapters, including objectives, reveals, ending states, costs, modes, and hooks;
- a short recent-pattern history of chapter modes, obstacles, conflict turns, emotional anchors, side-character moves, and ending-hook mechanisms;
- an adaptive whole-book word budget;
- a late-book ending-convergence contract; and
- explicit causal rules telling the model to inherit prior consequences, create preconditions for the next chapter, preserve later reveals, avoid accidental structural repetition, and write toward the remaining manuscript length.

The horizon does not invent new canon or alter the outline. It is a temporary causal contract generated from the existing outline, chapter plans, summaries, continuity checkpoints, and saved word counts. Future chapters are presented as commitments to prepare rather than scenes to consume early.

`NOVEL_NARRATIVE_LOOKAHEAD_CHAPTERS` defaults to 3 and is clamped from 1 to 6. Three chapters is intentionally modest: it gives the writer enough future pressure to plant setup and preserve causality without flooding a local model with distant material that should not dominate the current scene.

### Adaptive novel-length pacing

Each chapter prompt also ends with a compact boundary reminder: its current objective, stopping
state and hook trigger, and the next and final chapters' reserved objectives. This addresses local
models that mistake the whole-book ending promise or a hook's `next_problem` for events to finish
inside the current chapter. Planning, drafting, expansion, revision, critique, and editing receive
the reminder; critique is instructed to flag premature payoffs. It remains a prompt safeguard,
not a deterministic proof of temporal consistency. Review the generated prose and final QA.

Fixed per-chapter minimums are not enough to guarantee a full-length manuscript. If early chapters consistently land near the minimum, the book can finish tens of thousands of words below its stated target even though every individual chapter passed validation.

The narrative horizon therefore calculates the manuscript's live pace before each chapter: completed words, remaining words, remaining chapters, required average chapter length from this point forward, an adaptive target clamped to the configured chapter range, and whether the overall target is still reachable at the configured maximum.

The worker also adds a bounded enforcement layer. The existing static-minimum expansion runs first. If that chapter still falls below the live adaptive target, one supplemental expansion pass is allowed by default. That pass explicitly asks for dramatized action, dialogue, sensory specificity, reaction, complication, and consequence, while forbidding recap and repeated explanation as padding. It never changes the persisted run's configured chapter minimum or maximum.

`NOVEL_ADAPTIVE_LENGTH_ENFORCEMENT=0` disables the supplemental pass. `NOVEL_ADAPTIVE_LENGTH_MAX_EXTRA_PASSES` defaults to 1 and is capped at 2 so length recovery cannot create an uncontrolled rewrite loop.

### Late-book convergence

A different long-form failure appears near the end: local models often respond to rising stakes by inventing another faction, mystery, villain, system, relationship crisis, or fake climax. The novel becomes broader exactly when it should converge.

Starting around 70% progress, the narrative horizon exposes a finite closure budget derived from the existing continuity ledger and ending promise. It counts and surfaces open promises, unresolved threads, emotional loops, and trust fractures. From 80% onward the model is told to stop creating major new story debt unless it is already seeded or can pay off within the remaining chapters. With two chapters remaining—or after roughly 90% progress—the phase becomes `resolution_priority`.

Resolution priority protects one primary climax and one primary ending, encourages each remaining chapter to resolve or irreversibly transform existing story debt, reserves space for human/world-state aftermath, and explicitly prevents a sequel hook from substituting for closure of the current book.

## Character and subplot arc audit

Long books also fail when a supporting character, relationship fracture, emotional burden, or open promise disappears for so long that the model effectively forgets it. Novel Generator derives a bounded story-arc audit from the existing story bible, continuity ledger, completed chapter checkpoints, and future outline.

For each major character agenda the audit can surface the baseline want/fear/moral line, current character and ideology state, unresolved emotional loop, matching trust fractures, recent independent decisions, the last chapter that materially touched the character, how many chapters the arc has been dormant, and future outline chapters that appear to touch it again.

The audit also treats open promises, unresolved threads, emotional loops, and trust fractures as subplot lanes. Lanes that remain unresolved beyond `NOVEL_ARC_DORMANT_AFTER_CHAPTERS` are marked `dormant_unresolved`. Character presence requires the actual character name in persisted chapter state, while subplot matching ignores ubiquitous character-name tokens and prefers distinctive subplot vocabulary. This keeps generic themes such as trust, secrecy, or pressure from falsely resetting dormancy counters.

The model is explicitly told not to turn the audit into checklist writing: it should reactivate one or two relevant lanes, deliberately defer others, and never consume future outline reveals early merely to clear state.

This layer is read-only. It does not create a competing arc database or mutate the durable continuity ledger. `NOVEL_ARC_CONTEXT_MAX_CHARS` bounds the temporary arc packet, and the highest-risk dormant rows are retained first when the packet must shrink.

## Long-range chapter recall

A short rolling summary is good for scene-to-scene continuity but poor at callbacks. A clue, promise, object, lie, or relationship choice introduced in chapter 4 may become important again in chapter 29 after it has long since fallen outside the rolling window.

The chapter-recall compiler searches completed chapter summaries, outline summaries, and irreversible story turns using the current outline/plan as its focus. It always preserves a small number of recent chapters and can additionally retrieve a few older `relevant_callback` chapters whose distinctive terms overlap the current task. The retrieved rows contain summaries and story-turn state rather than old raw prose.

This is deliberately deterministic and local-first: no embedding service or extra model call is required. The model is told not to recap recalled material, not to rediscover facts the protagonist already knows, and to make callbacks create new leverage, emotion, meaning, or consequence rather than replaying the original beat.

`NOVEL_CHAPTER_RECALL_MAX_CHARS` bounds this packet. `NOVEL_CHAPTER_RECALL_RECENT` controls guaranteed recent history and `NOVEL_CHAPTER_RECALL_RELEVANT` controls how many older callback candidates may be selected.

## Whole-manuscript developmental context

Developmental rewrite planning needs a view of every chapter, but it does not need every sentence of every chapter. Passing a complete 80,000–150,000-word draft through one local-model request can overwhelm useful attention even when the model technically accepts the context length.

For that stage, the worker builds a bounded chapter-capsule map. Every chapter remains represented, but full prose is replaced with the chapter summary, outline summary, irreversible story turn, selected QA signals, and—when the budget allows—short opening and closing excerpts. If the book is too large for the detailed form, the compiler degrades detail uniformly across all chapters before dropping structural information. This keeps whole-book coverage while reserving context for diagnosis and rewrite planning.

Saved chapter prose is never changed by this process. The capsules are a temporary prompt view only.

## Bounded whole-manuscript QA

The manuscript-QA stage previously had a subtler version of the same problem: it serialized every chapter summary, full continuity update, story turn, and QA object. Because continuity updates can contain cumulative state, a 64-chapter book could repeat the same promises, trust fractures, and state maps dozens of times.

The QA stage now receives a separate bounded chapter map. Every chapter stays represented, while each row retains the editorial signals that matter most: summary, word count, story turn, chapter outcome, state-change signals, ideology shifts, selected trust/emotional/side-character state, genre state, system transitions, chapter QA scores/warnings, and bounded literal final-prose evidence. Detail degrades uniformly before chapter coverage is reduced.

Whole-book QA also receives the final unresolved arc and ending-debt audits. Final QA runs after the final-edit integrity guard, so a rolled-back edit cannot be evaluated as though it were accepted manuscript prose.

`NOVEL_MANUSCRIPT_QA_CONTEXT_MAX_CHARS` defaults to 70,000 characters independently from the developmental rewrite budget.

### Whole-book quality trends

Individually acceptable chapters can still form a declining manuscript. The whole-book QA prompt therefore receives a deterministic trend audit derived from persisted chapter QA/state.

The audit compares the first and last thirds, tracks chapter-length drift, identifies sustained changes in quality scores, finds repeated chapter modes and ending-hook types, surfaces recurring QA warnings, and records chapters still marked revision-required. Score direction is explicit: craft/continuity measures are generally higher-is-better, while repetition risk, technical-escalation fatigue, and cuttable-chapter risk are lower-is-better. A normalized `quality_direction_delta` always uses negative values for degradation and positive values for improvement.

Trend signals are advisory. They tell the manuscript editor where to inspect sustained drift rather than forcing every act into identical pacing or tone.

## Provider context, structured output, and completion capacity

Ollama calls set both the context window and a provider-wide completion ceiling:

- `OLLAMA_NUM_CTX=32768`
- `OLLAMA_NUM_PREDICT=8192`

OpenAI-compatible local servers expose a provider-wide completion ceiling through `OPENAI_COMPATIBLE_MAX_TOKENS`. Their model context window is not safely inferable from every frontend, so `OPENAI_COMPATIBLE_CONTEXT_TOKENS=0` means unknown by default. If the real loaded-model window is known, setting a non-zero value enables application-side headroom/context-utilization logic; the hint is never sent as an OpenAI request field.

Structured stages use stage-specific Pydantic JSON Schemas when supported. Ollama receives the schema through native `format`. OpenAI-compatible servers receive `response_format.type = json_schema`, with independent 400/422 compatibility fallback for schema controls and `max_tokens`. Application-side Pydantic validation and JSON repair remain authoritative.

### Stage-aware provider output ceilings

The configured provider ceilings are maximums. With `NOVEL_STAGE_OUTPUT_BUDGETS_ENABLED=1`, each supervised call can lower that ceiling to match the stage:

- prose stages: up to 8,192 tokens;
- outline/chunk, manuscript QA, publication readiness, developmental rewrite: up to 6,144;
- story bible: up to 4,096;
- chapter plan, critique, continuity update: up to 3,072;
- summary: up to 2,048;
- other stages: up to 4,096.

The per-call budget uses a private application marker that provider clients strip before sending chat messages. It can never raise the configured provider-wide maximum.

With `NOVEL_CONTEXT_HEADROOM_ENABLED=1` and a known provider context size, the same effective stage budget is reserved before generation. If the window is small, the reserve and provider cap are lowered together. Optional derived blocks can be shed in a fixed priority order while current prose, canon, durable continuity, and final ending-debt evidence remain protected.

The two controls are independent: disabling headroom does not disable stage provider caps, and disabling stage caps does not prevent headroom from reserving against the configured global provider output maximum.

### Output truncation recovery

Provider stop telemetry is used as a correctness signal. If a prose-producing stage explicitly ends because the output limit was reached, bounded continuation recovery uses clipped task guidance plus the prose tail, de-duplicates repeated seams, rejects obvious chapter restarts, and records each continuation as a normal supervised attempt.

If the continuation budget is exhausted while the provider still reports a length stop, the stage fails explicitly rather than accepting incomplete prose.

## Prompt and provider telemetry

Every supervised provider attempt records safe input-size telemetry alongside the existing provider/model/stage timing data. Metadata can include total input characters, a deliberately rough character-based token estimate, message count, largest message size, configured context size when known, headroom decisions, effective provider output budget, and estimated context utilization.

When a provider exposes actual generation metrics, the successful attempt is enriched under `metadata.provider_metrics`. Ollama can supply actual prompt/eval token counts, durations, throughput, stop reason, and actual context utilization. OpenAI-compatible servers can supply prompt/completion/total tokens, cached and reasoning-token details when reported, finish reason, response model, and context utilization when a compatible context hint is configured.

The manuscript text itself is not copied into telemetry. Metric extraction and persistence are fail-open: observability cannot turn a successful model call into a failed run.

## Deterministic long-form benchmark

Run:

```bash
python -m novel_generator.services.longform_benchmark
```

The benchmark builds a deliberately difficult synthetic 32-chapter book and checks cross-cutting long-form properties including continuity compaction, buried callback recall, dormant unresolved character detection, visibility of future arc touches, adaptive pacing, late-book resolution priority, bounded all-chapter developmental coverage, bounded all-chapter QA coverage, and detection of deliberate final-third quality/length drift.

It makes no model call and requires no embeddings or hosted service. A failure indicates a long-form architecture regression even when isolated helper tests still pass. Dedicated reliability tests separately cover schema/provider-control fallbacks, context headroom, stage output caps, output truncation recovery, final-edit rollback, final-prose evidence, ending debt, and reconciliation.

## Configuration

```env
OLLAMA_NUM_CTX=32768
OLLAMA_NUM_PREDICT=8192
OLLAMA_STRUCTURED_TEMPERATURE=0.2
OPENAI_COMPATIBLE_CONTEXT_TOKENS=0
OPENAI_COMPATIBLE_MAX_TOKENS=8192
NOVEL_MEMORY_ENABLED=1
NOVEL_MEMORY_MAX_CHARS=14000
NOVEL_MANUSCRIPT_CONTEXT_MAX_CHARS=70000
NOVEL_MANUSCRIPT_QA_CONTEXT_MAX_CHARS=70000
NOVEL_NARRATIVE_LOOKAHEAD_CHAPTERS=3
NOVEL_ARC_CONTEXT_ENABLED=1
NOVEL_ARC_CONTEXT_MAX_CHARS=12000
NOVEL_ARC_DORMANT_AFTER_CHAPTERS=5
NOVEL_CHAPTER_RECALL_ENABLED=1
NOVEL_CHAPTER_RECALL_MAX_CHARS=9000
NOVEL_CHAPTER_RECALL_RECENT=2
NOVEL_CHAPTER_RECALL_RELEVANT=4
NOVEL_ADAPTIVE_LENGTH_ENFORCEMENT=1
NOVEL_ADAPTIVE_LENGTH_MAX_EXTRA_PASSES=1
NOVEL_CONTEXT_HEADROOM_ENABLED=1
NOVEL_CONTEXT_HEADROOM_RESERVE_TOKENS=8192
NOVEL_STAGE_OUTPUT_BUDGETS_ENABLED=1
NOVEL_TRUNCATION_RECOVERY_ENABLED=1
NOVEL_TRUNCATION_MAX_CONTINUATIONS=2
NOVEL_TRUNCATION_CONTEXT_MAX_CHARS=18000
```

`NOVEL_MEMORY_MAX_CHARS` is the compact-JSON character budget for continuity portions of chapter-level prompts and is clamped to 4,000–50,000 characters. Developmental and manuscript-QA context budgets are independently clamped to 30,000–160,000 characters. Narrative lookahead is clamped to 1–6 chapters. Arc context is clamped to 4,000–30,000 characters and its dormancy threshold to 2–16 chapters. Recall context is clamped to 3,000–24,000 characters, with recent/relevant counts capped at 4/8. Adaptive extra passes are capped at 2. `OLLAMA_NUM_CTX` is validated from 2,048–262,144 tokens. Provider output ceilings are validated by settings and can only be lowered by per-stage controls.

Runtime context integrations are fail-open: if a future prompt builder changes serialization format and a compiler cannot safely replace or inject a context block, the original prompt is used instead of failing the generation run. Reliability layers that protect against silent corruption—persistent output truncation, destructive final edits, and false publication readiness—remain explicit rather than silently accepting bad state.

For local 8B–20B models, the defaults intentionally leave most useful attention for the current task while retaining enough book-level state to protect character, canon, timeline, unresolved threads, subplots, callbacks, ending convergence, quality drift, and manuscript-length drift.
