# Bounded novel memory

Long novels create a different context problem than short-form generation: the durable continuity ledger grows chapter after chapter, while local models still have a finite useful attention budget. Sending the entire accumulated ledger into every chapter-level prompt eventually spends more context on old resolved history than on the facts that matter for the current scene.

Novel Generator keeps the full continuity ledger as the canonical stored state. The worker now compiles a read-only memory packet for chapter-level planning, drafting, critique, revision, expansion, developmental revision, publication polishing, and final editing once the serialized ledger exceeds a configured size.

The compiler prioritizes current world state, chapter-relevant character state, open promises and threads, trust fractures, emotional loops, entity and system state, recent timeline events, system transitions, side-character decisions, civilian pressure, and a small amount of resolved history. Selection is deterministic: lexical relevance to the current chapter plan/outline and recent context is combined with recency. The canonical ledger is never truncated or mutated.

The continuity-update stage intentionally still receives the complete ledger. This preserves the cumulative state machine and prevents a compact prompt view from accidentally becoming the persisted source of truth.

## Configuration

```env
NOVEL_MEMORY_ENABLED=1
NOVEL_MEMORY_MAX_CHARS=14000
```

`NOVEL_MEMORY_ENABLED=0` disables the runtime prompt compiler. `NOVEL_MEMORY_MAX_CHARS` is the approximate JSON character budget for the continuity portion of chapter-level prompts and is clamped to a safe range of 4,000 to 50,000 characters.

Small ledgers pass through unchanged. Compaction starts only after the ledger exceeds the configured budget. The runtime integration is fail-open: if a future prompt builder changes serialization format and the compiler cannot safely replace the ledger block, the original prompt is used instead of failing the generation run.

For local 8B–20B models, the default 14,000-character memory budget is intended to leave more attention for the chapter outline, plan, prose, style profile, and current scene while retaining the continuity facts most likely to prevent character, canon, timeline, and unresolved-thread drift.
