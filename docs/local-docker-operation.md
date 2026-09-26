# Local Docker operation

For an existing workstation checkout with `data/novel_generator.db` and `artifacts/`, use:

```powershell
docker desktop start
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.local.yml ps
```

The local override preserves those directories with bind mounts and restarts exited services unless deliberately stopped. Docker Desktop must itself be running. Do not run the native worker against the same database at the same time.

In Provider Settings, the saved Ollama URL must be `http://host.docker.internal:11434`. An existing database retains its saved provider URL; changing an environment variable does not overwrite it. `127.0.0.1` inside a container refers to that container.

`/api/health/live` checks web-process liveness without waiting for external model providers. `/api/health` retains detailed database/provider diagnostics. A healthy web process is not proof that a book has completed.

For an isolated, real-model pipeline qualification inside the same image:

```powershell
docker compose -f docker-compose.yml -f docker-compose.local.yml exec -T worker python -m novel_generator.services.local_verification --model gemma4:latest --review-model gemma4:latest --revision-model qwen2.5:7b --base-url http://host.docker.internal:11434 --chapters 2 --words-per-chapter 500 --output-dir /app/artifacts/container-qualification
```

Use a fresh output directory each time. Inspect `report.json`: both `pipeline_complete` and `autonomous_checks_passed` must be true. A short successful qualification proves execution through export for that sample, not novel-scale quality.
