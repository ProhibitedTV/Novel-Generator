# Novel Generator

Novel Generator is a self-hosted, Ollama-first writing studio for long-form fiction. It lets a single author create reusable projects, queue background generation runs, stream progress in the browser, persist chapter-by-chapter state, and export finished manuscripts as Markdown and DOCX.

![Updated product preview](docs/screenshots/dashboard-preview.svg)

## What It Ships

- FastAPI backend with a server-rendered UI
- SQLite-first persistence with Alembic migrations
- Background worker process for queued generation runs
- Ollama provider integration with model discovery and health checks
- Chapter-level checkpointing and regeneration from any chapter onward
- Standard developmental planning plus a final chapter edit pass before export
- Markdown and DOCX artifact export
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

## Architecture

- `src/novel_generator/routers`: HTTP routes for the API and UI
- `src/novel_generator/services`: Ollama integration, prompts, pipeline, exports, and worker logic
- `src/novel_generator/repositories.py`: database-facing orchestration helpers
- `alembic/`: migration environment and schema history

The generation pipeline is designed to finish a complete book package, even for 32- and 64-chapter runs:

1. Create or reuse an outline.
2. Plan a chapter.
3. Draft the chapter.
4. Summarize it for continuity memory.
5. Persist progress after each step.
6. Run manuscript QA after the full draft is assembled.
7. Build a standard developmental rewrite plan and revised-outline report.
8. Apply targeted developmental revision waves to chapters marked for structural action.
9. Line-edit each saved chapter for polish while preserving approved story state.
10. Run final manuscript QA, then export Markdown, DOCX, and QA artifacts.

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
