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
- an adaptive whole-book word budget; and
- explicit causal rules telling the model to inherit prior consequences, create preconditions for the next chapter, preserve later reveals, avoid accidental structural repetition, and write toward the remaining manuscript length.

The horizon does not invent new canon or alter the outline. It is a temporary causal contract generated from the existing outline, chapter plans, summaries, continuity checkpoints, and saved word counts. Future chapters are presented as commitments to prepare rather than scenes to consume early.

`NOVEL_NARRATIVE_LOOKAHEAD_CHAPTERS` defaults to 3 and is clamped from 1 to 6. Three chapters is intentionally modest: it gives the writer enough future pressure to plant setup and preserve causality without flooding a local model with distant material that should not dominate the current scene.

### Adaptive novel-length pacing

Fixed per-chapter minimums are not enough to guarantee a full-length manuscript. If early chapters consistently land near the minimum, the book can finish tens of thousands of words below its stated target even though every individual chapter passed validation.

The narrative horizon therefore calculates the manuscript's live pace before each chapter: completed words, remaining words, remaining chapters, required average chapter length from this point forward, an adaptive target clamped to the configured chapter range, and whether the overall target is still reachable at the configured maximum. The model is instructed to aim near the adaptive target rather than treating the minimum as the default when the book is behind pace.

This is guidance, not destructive padding. Expansion still favors dramatized action, dialogue, sensory detail, emotional reaction, civilian texture, and consequence rather than recap or filler.

## Character and subplot arc audit

Long books also fail when a supporting character, relationship fracture, emotional burden, or open promise disappears for so long that the model effectively forgets it. Novel Generator now derives a bounded story-arc audit from the existing story bible, continuity ledger, completed chapter checkpoints, and future outline.

For each major character agenda the audit can surface the baseline want/fear/moral line, current character and ideology state, unresolved emotional loop, matching trust fractures, recent independent decisions, the last chapter that materially touched the character, how many chapters the arc has been dormant, and future outline chapters that appear to touch it again.

The audit also treats open promises, unresolved threads, emotional loops, and trust fractures as subplot lanes. Lanes that remain unresolved beyond `NOVEL_ARC_DORMANT_AFTER_CHAPTERS` are marked `dormant_unresolved`. The model is explicitly told not to turn that into checklist writing: it should reactivate one or two relevant lanes, deliberately defer others, and never consume future outline reveals early merely to clear state.

This layer is read-only. It does not create a competing arc database or mutate the durable continuity ledger. `NOVEL_ARC_CONTEXT_MAX_CHARS` bounds the temporary arc packet, and the highest-risk dormant rows are retained first when the packet must shrink.

## Whole-manuscript developmental context

Developmental rewrite planning needs a view of every chapter, but it does not need every sentence of every chapter. Passing a complete 80,000–150,000-word draft through one local-model request can overwhelm useful attention even when the model technically accepts the context length.

For that stage, the worker builds a bounded chapter-capsule map. Every chapter remains represented, but full prose is replaced with the chapter summary, outline summary, irreversible story turn, selected QA signals, and—when the budget allows—short opening and closing excerpts. If the book is too large for the detailed form, the compiler degrades detail uniformly across all chapters before dropping structural information. This keeps whole-book coverage while reserving context for diagnosis and rewrite planning.

Saved chapter prose is never changed by this process. The capsules are a temporary prompt view only.

## Bounded whole-manuscript QA

The manuscript-QA stage previously had a subtler version of the same problem: it serialized every chapter summary, full continuity update, story turn, and QA object. Because continuity updates can contain cumulative state, a 64-chapter book could repeat the same promises, trust fractures, and state maps dozens of times.

The QA stage now receives a separate bounded chapter map. Every chapter stays represented, while each row retains the editorial signals that matter most: summary, word count, story turn, chapter outcome, state-change signals, ideology shifts, selected trust/emotional/side-character state, genre state, system transitions, and chapter QA scores/warnings. Detail degrades uniformly from detailed to compact to minimal before chapter coverage is reduced.

Whole-book QA also receives the final unresolved arc audit, making abandoned character/subplot lanes visible to the editor without requiring raw chapter prose or repeated cumulative ledgers.

`NOVEL_MANUSCRIPT_QA_CONTEXT_MAX_CHARS` defaults to 70,000 characters independently from the developmental rewrite budget.

## Ollama context and structured output

The native Ollama chat API accepts model runtime options including `num_ctx` and also supports JSON response formatting. Novel Generator sends an explicit context size on Ollama generation calls instead of depending on the server or model default. The default is 32,768 tokens, which is large enough for the bounded chapter and developmental contexts used by this pipeline while remaining configurable for machines with tighter RAM or VRAM limits.

Structured pipeline prompts such as story bibles, outlines, chapter plans, critiques, continuity updates, manuscript QA, and JSON repair requests are detected automatically from their system instructions. Those calls use Ollama's native `format: "json"` mode and a lower structured-generation temperature. This reduces the chance that a local model wraps JSON in commentary, markdown, or malformed free-form text before the application's existing validation and repair layer sees it.

OpenAI-compatible local servers receive `response_format: {"type": "json_object"}` for the same structured stages. If a compatible server rejects that option with HTTP 400/422, the client retries the same request without `response_format` rather than breaking the run.

The default structured temperature is `0.2`. It is intentionally lower than normal prose generation because these stages are schema-following control work rather than creative drafting. Prose calls keep the model's normal generation behavior.

If a selected model supports less context than the configured value, or the machine cannot comfortably run that context size, lower `OLLAMA_NUM_CTX`. If the model and hardware support substantially more context, it can be raised up to the application validation limit.

## Prompt-size telemetry

Every supervised provider attempt records safe input-size telemetry alongside the existing provider/model/stage timing data. The attempt metadata includes total input characters, a deliberately rough character-based token estimate, message count, largest message size, configured context size when known, and estimated context utilization percentage.

The manuscript text itself is not copied into telemetry. These measurements are intended to make local-model tuning empirical: a slow or weak stage can be correlated with context pressure without persisting another copy of the author's prose.

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
```

`NOVEL_MEMORY_MAX_CHARS` is the compact-JSON character budget for the continuity portion of chapter-level prompts and is clamped to 4,000–50,000 characters. `NOVEL_MANUSCRIPT_CONTEXT_MAX_CHARS` controls developmental rewrite capsules and is clamped to 30,000–160,000 characters. `NOVEL_MANUSCRIPT_QA_CONTEXT_MAX_CHARS` independently controls the whole-book QA map over the same range. `NOVEL_NARRATIVE_LOOKAHEAD_CHAPTERS` controls causal lookahead and is clamped to 1–6 chapters. `NOVEL_ARC_CONTEXT_MAX_CHARS` controls the character/subplot audit and is clamped to 4,000–30,000 characters. `NOVEL_ARC_DORMANT_AFTER_CHAPTERS` determines when an unresolved lane becomes a dormancy warning and is clamped to 2–16 chapters. `OLLAMA_NUM_CTX` is validated from 2,048–262,144 tokens. `OLLAMA_STRUCTURED_TEMPERATURE` is validated from 0.0–2.0.

The runtime integrations are fail-open: if a future prompt builder changes serialization format and a compiler cannot safely replace or inject a context block, the original prompt is used instead of failing the generation run.

For local 8B–20B models, the defaults intentionally leave most of the attention budget for the current scene and prose while retaining enough book-level state to prevent character, canon, timeline, unresolved-thread, subplot, and manuscript-length drift.