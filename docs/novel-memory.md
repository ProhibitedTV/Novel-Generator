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

The QA stage now receives a separate bounded chapter map. Every chapter stays represented, while each row retains the editorial signals that matter most: summary, word count, story turn, chapter outcome, state-change signals, ideology shifts, selected trust/emotional/side-character state, genre state, system transitions, and chapter QA scores/warnings. Detail degrades uniformly from detailed to compact to minimal before chapter coverage is reduced.

Whole-book QA also receives the final unresolved arc audit, making abandoned character/subplot lanes visible to the editor without requiring raw chapter prose or repeated cumulative ledgers.

`NOVEL_MANUSCRIPT_QA_CONTEXT_MAX_CHARS` defaults to 70,000 characters independently from the developmental rewrite budget.

### Whole-book quality trends

Individually acceptable chapters can still form a declining manuscript. The whole-book QA prompt therefore receives a deterministic trend audit derived from persisted chapter QA/state.

The audit compares the first and last thirds of the manuscript, tracks chapter-length drift, identifies sustained changes in quality scores, finds repeated chapter modes and ending-hook types, surfaces recurring QA warnings, and records chapters still marked revision-required. Score direction is explicit: craft/continuity measures are generally higher-is-better, while repetition risk, technical-escalation fatigue, and cuttable-chapter risk are lower-is-better. A normalized `quality_direction_delta` always uses negative values for degradation and positive values for improvement.

Trend signals are advisory. They tell the manuscript editor where to inspect sustained drift rather than forcing every act into identical pacing or tone.

## Ollama context and structured output

The native Ollama chat API accepts model runtime options including `num_ctx` and also supports JSON response formatting. Novel Generator sends an explicit context size on Ollama generation calls instead of depending on the server or model default. The default is 32,768 tokens, which is large enough for the bounded chapter and developmental contexts used by this pipeline while remaining configurable for machines with tighter RAM or VRAM limits.

Structured pipeline prompts such as story bibles, outlines, chapter plans, critiques, continuity updates, manuscript QA, and JSON repair requests are detected automatically from their system instructions. Those calls use Ollama's native `format: "json"` mode and a lower structured-generation temperature. This reduces the chance that a local model wraps JSON in commentary, markdown, or malformed free-form text before the application's existing validation and repair layer sees it.

OpenAI-compatible local servers receive `response_format: {"type": "json_object"}` for the same structured stages. If a compatible server rejects that option with HTTP 400/422, the client retries the same request without `response_format` rather than breaking the run.

The default structured temperature is `0.2`. It is intentionally lower than normal prose generation because these stages are schema-following control work rather than creative drafting. Prose calls keep the model's normal generation behavior.

If a selected model supports less context than the configured value, or the machine cannot comfortably run that context size, lower `OLLAMA_NUM_CTX`. If the model and hardware support substantially more context, it can be raised up to the application validation limit.

## Prompt and provider telemetry

Every supervised provider attempt records safe input-size telemetry alongside the existing provider/model/stage timing data. Before the request, metadata includes total input characters, a deliberately rough character-based token estimate, message count, largest message size, configured context size when known, and estimated context utilization percentage.

When a provider exposes actual generation metrics, the successful attempt is then enriched under `metadata.provider_metrics` without replacing the pre-request telemetry. Ollama can supply actual prompt/eval token counts, prompt/eval/load/total durations, prompt/completion throughput, stop reason, and actual context-window utilization. OpenAI-compatible servers can supply prompt/completion/total tokens, cached and reasoning-token details when reported, finish reason, and the response model identifier.

The manuscript text itself is not copied into telemetry. Metric extraction and persistence are fail-open: observability can never turn a successful model call into a failed generation run.

## Deterministic long-form benchmark

Run:

```bash
python -m novel_generator.services.longform_benchmark
```

The benchmark builds a deliberately difficult synthetic 32-chapter book and currently checks nine cross-cutting properties in one run: continuity compaction, buried callback recall, dormant unresolved character detection, visibility of future arc touches, adaptive pacing, late-book resolution priority, bounded all-chapter developmental coverage, bounded all-chapter QA coverage, and detection of deliberate final-third quality/length drift.

It makes no model call and requires no embeddings or hosted service. A failure indicates a long-form architecture regression even when isolated helper tests still pass. See `docs/longform-benchmark.md` for the report fields and interpretation guidance.

## Configuration

```env
OLLAMA_NUM_CTX=32768
OLLAMA_STRUCTURED_TEMPERATURE=0.2
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
```

`NOVEL_MEMORY_MAX_CHARS` is the compact-JSON character budget for the continuity portion of chapter-level prompts and is clamped to 4,000–50,000 characters. `NOVEL_MANUSCRIPT_CONTEXT_MAX_CHARS` controls developmental rewrite capsules and is clamped to 30,000–160,000 characters. `NOVEL_MANUSCRIPT_QA_CONTEXT_MAX_CHARS` independently controls the whole-book QA map over the same range. `NOVEL_NARRATIVE_LOOKAHEAD_CHAPTERS` controls causal lookahead and is clamped to 1–6 chapters. `NOVEL_ARC_CONTEXT_MAX_CHARS` controls the character/subplot audit and is clamped to 4,000–30,000 characters. `NOVEL_ARC_DORMANT_AFTER_CHAPTERS` determines when an unresolved lane becomes a dormancy warning and is clamped to 2–16 chapters. `NOVEL_CHAPTER_RECALL_MAX_CHARS` is clamped to 3,000–24,000 characters, while recent and relevant recall counts are capped at 4 and 8 respectively. `NOVEL_ADAPTIVE_LENGTH_MAX_EXTRA_PASSES` is capped at 2. `OLLAMA_NUM_CTX` is validated from 2,048–262,144 tokens. `OLLAMA_STRUCTURED_TEMPERATURE` is validated from 0.0–2.0.

The runtime integrations are fail-open: if a future prompt builder changes serialization format and a compiler cannot safely replace or inject a context block, the original prompt is used instead of failing the generation run.

For local 8B–20B models, the defaults intentionally leave most of the attention budget for the current scene and prose while retaining enough book-level state to prevent character, canon, timeline, unresolved-thread, subplot, callback, ending-convergence, quality-drift, and manuscript-length drift.