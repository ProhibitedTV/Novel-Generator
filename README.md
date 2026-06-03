# Novel Generator

Novel Generator is a self-hosted, Ollama-first writing studio for long-form fiction. It lets a single author create reusable book projects, queue background generation runs, inspect long-run progress in the browser, recover from model or worker failures, and export complete manuscripts as Markdown and DOCX layout helpers.

The product is built around one practical goal: get to a complete book draft, then make the draft increasingly reviewable, editable, and polished without losing the run history that produced it.

![Updated product preview](docs/screenshots/dashboard-preview.svg)

## What It Ships

- FastAPI backend with a server-rendered UI
- SQLite-first persistence with Alembic migrations
- Background worker process for queued generation runs
- Ollama provider integration with model discovery and health checks
- Provider routing, per-stage attempt tracking, stale-worker recovery, and failed-run resume
- Run confidence views for stage, event, chapter, word, provider, and artifact progress
- Outline review workspace for long outlines, including 32- and 64-chapter projects
- Chapter-level checkpointing, in-place resume, and regeneration from any chapter onward
- Draft, balanced, strict, and publication quality profiles
- Standard developmental planning, targeted revision waves, final chapter editing, and optional publication-mode humanization/compression passes
- Markdown and DOCX manuscript exports plus publication layout helpers with required front matter
- Docker Compose setup for self-hosting

## Quick Start

1. Copy `.env.example` to `.env`.
2. Set `OLLAMA_BASE_URL` and `DEFAULT_MODEL`.
3. Start the app:

```bash
docker compose up --build
```

4. Open [http://localhost:8000](http://localhost:8000).

If you want Ollama in the same compose stack, enable the optional profile and point `OLLAMA_BASE_URL` at `http://ollama:11434`:

```bash
docker compose --profile ollama up --build
```

## First Run

1. Open the dashboard and create a project with a premise, target word count, chapter count, genre, and model.
2. Confirm the provider health check before queueing. For high chapter counts, use outline approval so you can review the book shape before drafting starts.
3. Choose a quality profile:
   - `draft`: fastest route to a complete manuscript. Best for exploring a premise.
   - `balanced`: default behavior. Standard QA, developmental planning, targeted revisions, and final chapter editing.
   - `strict`: stronger revision thresholds for a more conservative editorial pass.
   - `publication`: highest-cost path. Forces outline approval, developmental rewrite, character humanization, prose compression, final editing, and a final publication-readiness QA gate.
4. Let the worker run. The run page shows current stage, last event, provider route, chapter progress, word progress, attempts, artifacts, and recovery guidance.
5. If a run fails, use resume from checkpoint when available. Already completed chapters, summaries, continuity updates, attempts, and events are preserved.
6. Export only after reviewing the final QA report. Publication exports are layout helpers and require real front matter instead of placeholders.

## Quality Profiles

The profiles trade speed for editorial pressure.

| Profile | Best for | Behavior |
| --- | --- | --- |
| `draft` | Fast exploration | Defers non-blocking polish so long runs reach a complete manuscript sooner. |
| `balanced` | Normal complete drafts | Uses the standard chapter QA, developmental rewrite planning, targeted revision waves, and final edit pass. |
| `strict` | More cautious drafts | Tightens revision triggers and enables developmental rewrite by default. |
| `publication` | Serious editorial review | Drafts long, then cuts. Adds character-private-life requirements, scene-variety rules, motif budgets, humanization revisions, compression, final editing, and a readiness score. |

Publication mode is intentionally expensive. It is designed to address generated-manuscript weaknesses such as repeated atmosphere, looped crisis structure, allegorical characters, over-explained prose, thin ordinary human friction, and misleading publication exports. If the final readiness gate still finds major risk, the run completes as a reviewable manuscript labeled as needing editorial revision instead of claiming to be publication-ready.

## Model Benchmarks

The app can route individual runs to any model reported by Ollama. Benchmark results below are from a controlled local smoke through the app's provider path and attempt ledger; they are useful for routing decisions, not a general leaderboard.

Benchmark environment:

- Date: 2026-05-31
- Host: Windows 10 10.0.19045 with Docker Desktop 4.74.0 / WSL2, Docker Engine 29.4.3
- CPU: AMD Ryzen 7 2700X, 8 cores / 16 threads, up to 3.8 GHz
- RAM: 32 GiB
- GPU: NVIDIA GeForce RTX 3060, 12 GiB VRAM, driver 610.47
- App path: Docker Compose `web` container, temporary SQLite database, Ollama at `http://host.docker.internal:11434`
- Benchmark fixture: 2-chapter balanced project plus a 64-chapter long-context outline review probe

`gemma4:e4b` metadata:

- Official library page: [Ollama gemma4:e4b](https://ollama.com/library/gemma4%3Ae4b)
- Local Ollama metadata: `gemma4` family, 8.0B parameters, `Q4_K_M`, 131,072 token context
- Local model size: about 9.6 GB

| Model | Full benchmark time | Structured stages | Prose stages | 64-chapter review | JSON repair usage |
| --- | ---: | ---: | ---: | ---: | ---: |
| `gemma4:e4b` | 20.2 min | 15.2 min | 4.3 min | 79.8 sec | 1 story-bible repair |
| `qwen3:14b` | 70.1 min | 54.5 min | 13.2 min | 525.9 sec | 0 |
| `gpt-oss:20b` | 91.2 min | 65.6 min | 12.6 min | 534.1 sec | 0 |

Current routing guidance:

- Keep the configured default model unchanged until more end-to-end manuscripts are reviewed.
- `gemma4:e4b` is a strong manual choice for fast draft-profile runs and support stages.
- Watch story-bible generation closely with `gemma4:e4b`; the benchmark completed successfully, but one story-bible call needed JSON repair before validation.

## Local Development

Use Python 3.11+.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .[dev]
cp .env.example .env
uvicorn novel_generator.main:app --reload
python -m novel_generator.worker
pytest
```

On Windows PowerShell, activate the environment with `.venv\Scripts\Activate.ps1`.

## API Surface

- `GET /api/health`
- `GET /api/providers/ollama/status`
- `GET /api/providers/ollama/models`
- `POST /api/projects`
- `GET /api/projects/{id}`
- `PATCH /api/projects/{id}`
- `POST /api/runs`
- `GET /api/runs/{id}`
- `POST /api/runs/{id}/cancel`
- `POST /api/runs/{id}/rerun`
- `POST /api/runs/{id}/resume`
- `GET /api/runs/{id}/attempts`
- `GET /api/runs/{id}/events`
- `GET /api/artifacts/{id}/download`

## How Generation Works

- `src/novel_generator/routers`: HTTP routes for the API and UI
- `src/novel_generator/services`: Ollama integration, prompts, pipeline, exports, and worker logic
- `src/novel_generator/repositories.py`: database-facing orchestration helpers
- `alembic/`: migration environment and schema history

The generation pipeline is designed to finish a complete book package, even for 32- and 64-chapter runs:

1. Create or reuse a story bible and outline.
2. Pause for outline review when requested, or automatically for publication runs.
3. Plan each chapter, draft prose, critique the chapter, revise when profile thresholds require it, summarize for continuity, and update the continuity ledger.
4. Persist progress after each stage so the run can resume from checkpoints.
5. Record safe model-call attempts with provider, model, stage, timing, status, output length, and error metadata.
6. Run manuscript QA after the full draft is assembled.
7. Build a developmental rewrite plan and revised-outline report.
8. Apply targeted developmental revision waves to chapters marked for structural action.
9. For publication runs, supplement weak rewrite plans with deterministic QA findings, then run character humanization and prose compression waves.
10. Line-edit each saved chapter for clarity, sentence rhythm, transitions, and concrete on-page consequences.
11. Run final manuscript QA. Publication runs also receive scored readiness notes for concept/world, atmosphere, thematic ambition, plot coherence, character depth, prose control, repetition, and publication readiness.
12. Export Markdown, DOCX, QA reports, revised outlines, and publication layout helpers as artifacts.

## Publication Exports

Publication export profiles are layout helpers, not a promise that the manuscript is ready to sell. The export form requires:

- Author name
- Copyright year
- Publisher or imprint
- Dedication
- Author note

ISBN is optional. If ISBN is blank, the ISBN line is omitted. Bracketed placeholder text such as `[Author Name]` is rejected so generated exports cannot silently ship placeholder front matter.

## Self-Hosting Notes

- This release is intentionally optimized for a single-user deployment.
- It does not ship in-app auth. If you expose it publicly, place it behind a reverse proxy with authentication.
- SQLite is the default storage engine for easier local and home-lab deployments.

Additional docs:

- [Self-hosting](docs/self-hosting.md)
- [Backup and restore](docs/backup-and-restore.md)
- [Releasing](docs/releasing.md)

## Development Standards

- Apache-2.0 licensed
- Contributor Covenant code of conduct
- CI runs the pytest suite on pushes and pull requests

## Current Limits

- Ollama is the primary local-first backend. OpenAI-compatible routing can be configured separately when explicitly enabled.
- Runs are processed sequentially by default to avoid oversubscribing local hardware.
- Chapter regeneration creates a new run from the selected chapter onward instead of mutating history in place.
