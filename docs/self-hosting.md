# Self-Hosting

## Default Deployment

The default deployment uses:

- FastAPI web service
- Background worker
- SQLite database stored on a persistent volume
- Local artifact storage volume
- An external or colocated Ollama instance

Both the web process and worker run Alembic migrations automatically at startup.

## Recommended: App in Docker, Ollama on the Host

Create the environment file:

```bash
cp .env.example .env
```

If Ollama runs on the host machine, set this in `.env` before starting Compose:

```env
OLLAMA_BASE_URL=http://host.docker.internal:11434
DEFAULT_MODEL=llama3.1:8b
```

The copied `.env.example` uses `127.0.0.1`, which is correct for a native Python run but not for a Docker container trying to reach host Ollama. The included Compose file maps `host.docker.internal` through `host-gateway` for Linux Docker as well.

Make sure the selected model exists on the host:

```bash
ollama pull llama3.1:8b
```

Then start the app:

```bash
docker compose up --build
```

Open <http://localhost:8000> and verify:

```bash
curl -fsS http://localhost:8000/api/health
curl -fsS http://localhost:8000/api/providers/ollama/status
```

## Optional Colocated Ollama

If you want Ollama inside the same Compose stack, set:

```env
OLLAMA_BASE_URL=http://ollama:11434
DEFAULT_MODEL=llama3.1:8b
```

Start the optional profile:

```bash
docker compose --profile ollama up -d --build
```

A fresh Ollama container does not include the model, so pull it explicitly:

```bash
docker compose exec ollama ollama pull llama3.1:8b
```

Then check the app-facing provider status:

```bash
curl -fsS http://localhost:8000/api/providers/ollama/status
```

The repository's Compose file does not declare GPU passthrough for the `ollama` service. Configure GPU/accelerator access separately for your Docker platform, or run Ollama on the host and use the recommended deployment above.

## Provider Settings

The app's provider configuration page is available at:

<http://localhost:8000/settings/provider>

Ollama is enabled by default. The OpenAI-compatible provider is disabled until explicitly enabled. The UI can save provider base URLs, default models, API keys, and enable/disable state.

## Persistent Data

Docker Compose uses named volumes for:

- `app_data`: SQLite database data
- `app_artifacts`: generated manuscripts, QA reports, and other artifacts
- `ollama_data`: Ollama model data when the optional Ollama profile is used

Keep these volumes in your backup plan.

## Production Guidance

- Terminate TLS at a reverse proxy such as Caddy, Nginx, or Traefik.
- Put the app behind authentication before exposing it beyond a trusted network.
- Change `SECRET_KEY` from the development default.
- Keep the artifact and database volumes on persistent storage.
- If you change `DATABASE_URL`, update the volume and backup strategy accordingly.
- Keep `MAX_CONCURRENT_RUNS=1` unless your inference hardware is intentionally sized for overlapping generation workloads.

## Reverse Proxy Auth

This app intentionally does not ship with in-app authentication in v1. For public or semi-public deployments, use one of:

- Reverse proxy basic auth
- Forward auth through an identity provider
- VPN-only access

## Health Checks

- Web health: `GET /api/health`
- Ollama reachability: `GET /api/providers/ollama/status`
- Ollama model discovery: `GET /api/providers/ollama/models`
