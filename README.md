# Novel Generator

Novel Generator is a local-first web application for generating long-form novels from self-hosted or OpenAI-compatible language models. It combines structured story planning, chapter-by-chapter drafting, continuity tracking, editorial QA, resumable worker execution, and export tooling so a full book can be generated as a supervised pipeline instead of a single giant prompt.

## Highlights

- FastAPI web UI and API
- SQLite + Alembic persistence
- Background worker with resumable chapter checkpoints
- Ollama-first local generation with OpenAI-compatible provider support
- Stage-specific provider/model routing
- Structured story bible and chapter outline generation
- Chapter planning, drafting, critique, revision, summary, and continuity updates
- Publication-quality developmental rewrite, humanization, compression, and final edit passes
- Bounded long-form memory, causal horizons, long-range callback recall, character/subplot arc audits, and late-book convergence controls
- Adaptive whole-book pacing with bounded supplemental chapter expansion when the manuscript falls behind target
- Bounded whole-manuscript developmental and QA context for 32/64-chapter books
- Deterministic whole-book quality trend analysis for gradual late-book drift
- Native structured output for Ollama and supported OpenAI-compatible servers
- Actual provider token/throughput/stop telemetry when exposed by the local backend
- Deterministic 32-chapter long-form architecture benchmark
- Markdown and DOCX manuscript exports

## Quick start

### Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

Open `http://localhost:8000`.

### Local Python

Requires Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .[dev]
alembic upgrade head
uvicorn novel_generator.main:app --reload
```

Start the worker in another terminal:

```bash
python -m novel_generator.worker
```

## Long-form architecture benchmark

The repository includes a deterministic synthetic 32-chapter stress test for the long-form architecture. It makes no model call and does not require embeddings or a hosted service.

```bash
python -m novel_generator.services.longform_benchmark
```

The command checks bounded continuity memory, buried callback recall, dormant unresolved arcs, future-touch visibility, adaptive whole-book pacing, late-book resolution priority, bounded developmental/QA coverage, and whole-book quality-drift detection. It prints a JSON report and exits non-zero if any architectural invariant fails.

See `docs/longform-benchmark.md`, `docs/novel-memory.md`, and `docs/continuity-lifecycle.md` for details.

## How generation works

1. Build a story bible from the project brief.
2. Build a structured chapter outline. Long runs use chunked generation when appropriate.
3. Pause for outline review when requested, or automatically for publication runs.
4. Plan each chapter, draft prose, critique the chapter, revise when profile thresholds require it, summarize for continuity, and update the continuity ledger.
5. Persist progress after each stage so the run can resume from checkpoints.
6. Record safe model-call attempts with provider, model, stage, timing, status, output length, prompt-size telemetry, and provider usage/throughput metadata when available.
7. Run manuscript QA after the full draft is assembled, including unresolved-arc and whole-book quality-trend signals.
8. Build a developmental rewrite plan and revised-outline report.
9. Apply targeted developmental revision waves to chapters marked for structural action.
10. In publication mode, run humanization/compression passes and publication-readiness QA.
11. Finalize exports.

The long-form runtime keeps the complete outline, continuity ledger, chapter checkpoints, and saved prose as durable sources of truth. Bounded context compilers create temporary read views for local models so old history does not crowd the current scene out of the useful attention window.

## Providers

### Ollama

Ollama is the default local provider. Set:

```env
OLLAMA_BASE_URL=http://127.0.0.1:11434
DEFAULT_MODEL=llama3.1:8b
OLLAMA_NUM_CTX=32768
OLLAMA_STRUCTURED_TEMPERATURE=0.2
```

Structured control prompts use Ollama JSON mode automatically. Prose calls keep ordinary generation behavior. When Ollama reports performance metadata, successful stage attempts can record prompt/eval token counts, durations, throughput, stop reason, and actual context utilization without storing the manuscript text in telemetry.

### OpenAI-compatible endpoints

OpenAI-compatible local or external endpoints can be configured in the provider settings UI. Structured control prompts request JSON-object output when supported and gracefully fall back when a backend rejects that option. Usage and finish-reason metadata are retained when the endpoint reports them.

## Long-form context configuration

The main long-form controls are:

```env
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

See `.env.example` for the full configuration surface.

## Quality profiles

Runs can use different editorial profiles:

- **Draft** — prioritizes reaching a complete manuscript quickly.
- **Balanced** — standard revision and developmental behavior.
- **Strict** — tighter chapter revision thresholds.
- **Publication** — outline approval, long-draft/compression strategy, structural revision, humanization, final editing, and publication-readiness QA.

## Resilience

Generation runs persist checkpoints and stage attempts so interrupted work can resume instead of restarting the entire novel. Worker startup and stale-heartbeat recovery can requeue interrupted runs. Provider and parsing failures are recorded with stage/chapter context. Context shaping and observability layers are fail-open: a memory compiler or telemetry issue should never convert otherwise valid generation into a failed run.

## Tests

```bash
pytest
```

The test suite covers API/UI behavior, migrations, provider routing, generation checkpoints, continuity handling, prompt shaping, long-form memory/recall, adaptive pacing, ending convergence, quality trends, provider metrics, and the deterministic long-form benchmark.

## License

See `LICENSE`.