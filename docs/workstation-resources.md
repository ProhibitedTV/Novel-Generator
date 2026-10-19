# Workstation resources during long runs

Start with one generation run at a time (`MAX_CONCURRENT_RUNS=1`, the default).
Ollama runs on the host when using `docker-compose.local.yml`, so Docker memory
statistics alone do not include model memory. Check host RAM, GPU memory, and
free disk space after the model has loaded and while it is generating. Required
memory depends on the model, context window, and other applications; a successful
startup does not establish that enough memory remains for a complete book.

On Windows PowerShell:

```powershell
$system = Get-CimInstance Win32_OperatingSystem
[pscustomobject]@{
    FreeRAMGiB = [math]::Round($system.FreePhysicalMemory / 1MB, 1)
    FreeDiskGiB = [math]::Round((Get-PSDrive C).Free / 1GB, 1)
}
nvidia-smi
ollama ps
docker compose -f docker-compose.yml -f docker-compose.local.yml stats --no-stream
```

`nvidia-smi` requires an NVIDIA GPU and driver. For other GPUs, use the vendor's
monitoring tools. High GPU utilization is expected during inference. If host
memory is scarce, close unused applications or unload an idle model before
starting more work. Reducing the context window can reduce memory use, but may
prevent complete chapter comparisons from fitting; requalify the configuration
before relying on its editorial results. Container memory limits do not reserve
RAM for a native Ollama server.

## Storage and cleanup

Compose rotates each service's container logs at 10 MB and keeps at most three
files per container. This bounds log-file growth for the web, worker, and optional
Ollama service. It does not cap database, artifact, model, image, or build-cache
storage. Run events and editorial evidence remain in the database; container
logs retain only recent diagnostics.

Logging settings take effect when containers are recreated. Before an upgrade,
back up the database and artifacts. Let an active inference finish where
practical; interrupted calls must be recovered from their saved checkpoint.
Apply the settings with the same Compose files used to start the stack:

```powershell
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d
```

Inspect storage before deleting anything:

```powershell
docker system df
ollama list
```

Remove only identified unused models with `ollama rm <model-name>` and known
unused image IDs with `docker image rm <image-id>`. Keep all models referenced
by active run routes and a working deployment image for rollback. Avoid broad
Docker pruning on a workstation shared with other projects, particularly volume
pruning. Docker's reported reclaimed space may not immediately reduce its virtual
disk file on the host.

Keep the manuscript database, artifacts, checkpoints, revision history, and
backups until their contents have been verified and retention has been explicitly
decided. If space is running out, cancel the run through the app so it can stop at
a checkpoint before doing further maintenance. Do not delete Docker volumes or
manuscript files to free space for an active run.
