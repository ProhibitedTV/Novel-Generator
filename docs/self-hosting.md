# Self-Hosting

## Default Deployment

The default deployment uses:

- FastAPI web service
- Background worker
- SQLite database stored on a persistent volume
- Local artifact storage volume
- An external or colocated Ollama instance

Start with:

```bash
cp .env.example .env
docker compose up --build
```

## Optional Colocated Ollama

If you want Ollama inside the same compose stack:

1. Set `OLLAMA_BASE_URL=http://ollama:11434` in `.env`.
2. Start the `ollama` profile:

```bash
docker compose --profile ollama up --build
```

## Production Guidance

- Terminate TLS at a reverse proxy such as Caddy, Nginx, or Traefik.
- Put the app behind authentication before exposing it beyond a trusted network.
- Keep the artifact and database volumes on persistent storage.
- If you change `DATABASE_URL`, update the volume and backup strategy accordingly.

## Reverse Proxy Auth

This app intentionally does not ship with in-app authentication in v1. For public or semi-public deployments, use one of:

- Reverse proxy basic auth
- Forward auth through an identity provider
- VPN-only access

## Health Checks

- Web health: `GET /api/health`
- Ollama reachability: `GET /api/providers/ollama/status`
