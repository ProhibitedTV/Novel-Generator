# Workstation production run checklist

Target setup: Windows + Docker Desktop, one RTX 3060 with 12 GB VRAM, 32 GB system RAM, host Ollama, and the local Compose override. This is a qualification plan, not a measured speed or quality guarantee.

## Before starting

1. Finish downloading the exact `gemma4:26b-a4b` tag. Check `ollama list`.
2. Back up the local data/artifacts and .env, then rebuild both app services using the local override (see local-docker-operation.md).
3. Ensure the previous run is completed or explicitly canceled with saved prose recovered. Do not delete its project or database. Only one production run should own the local worker; avoid competing GPU jobs, including games, image generation and other agents.
4. Stop the worker for qualification. Use the **web** container for isolated CLI tests (it shares the artifacts mount), not a second production worker.

```cmd
docker compose -f docker-compose.yml -f docker-compose.local.yml stop worker
docker compose -f docker-compose.yml -f docker-compose.local.yml exec -T web python -m novel_generator.services.author_benchmark --models gemma4:12b gemma4:26b-a4b --base-url http://host.docker.internal:11434 --context-tokens 8192 --output-dir /app/artifacts/gemma26-audition-01
```

Compare anonymous prose before opening the model key. Short samples are not representative of long-context chapter-pair throughput. Check `ollama ps` during inference and available RAM/VRAM, not just installed file size. A 26B MoE does not fit its complete quantized weights in 12 GB VRAM; CPU offload and KV cache can dominate runtime. Do not enable 256K context on this workstation.

For a more representative isolated qualification, start at the existing 32768 context if stable, using 2 chapters of 2,500 words and a fresh directory:

```cmd
docker compose -f docker-compose.yml -f docker-compose.local.yml exec -T web python -m novel_generator.services.local_verification --model gemma4:26b-a4b --review-model gemma4:26b-a4b --revision-model gemma4:26b-a4b --base-url http://host.docker.internal:11434 --chapters 2 --words-per-chapter 2500 --context-tokens 32768 --output-dir /app/artifacts/gemma26-production-probe-01
```

Review report.json, actual prose and elapsed wall time. If the 26B cannot fit reliably, test a lower context only if all source prompts fit, or use the qualified 12B. Do not truncate source reviews. Test smaller-model reviewer routing separately; frequent model swapping can cost more than it saves. The audition is a comparison, not proof of editorial independence or accuracy. Keep thinking disabled for prose and avoid expensive optional image generation.

## Choose the production scope

The queue form now shows full chapter-pair reviews and an adjustable compute budget. For autonomous runs the number of comparisons in a clean final sweep is N*(N-1)/2, including neighboring pairs.

| Book scope | Chapters | Full pair reviews |
|---|---:|---:|
| Compact book (36,000 words) | 12 | 66 |
| Short novel (42,000 words) | 14 | 91 |
| Previous 60,000-word scale | 24 | 276 |

These are not time estimates. One clean sweep is only part of the work; initial drafts, summaries, continuity updates, structured QA, repairs, final edits, and model load/prefill also consume time. Use the measured probe and leave at least 25% of the available time for repair and review. For a fixed deadline, choose the compact scope if the probe is slow rather than disabling audits. The short novel is preferable only if the measured rate supports it.

Keep the author brief focused: a small principal cast, a single central conflict, a clearly irreversible climax, distinct chapter outcomes, and an aftermath that does not undo the climax. A complete shorter book is preferable to a longer unfinished one.

Select the exact installed model in the new project's saved defaults and run routes. Existing runs retain their own route snapshots; changing the global default does not retune them. Start the worker again after qualification:

```cmd
docker compose -f docker-compose.yml -f docker-compose.local.yml start worker
```

Queue just one run. Choose Autonomous only if full source-pair QA is wanted; it has no manual outline pause. Publication mode permits outline approval and layout helpers but does not provide the same full autonomous acceptance contract. Do not present either as a guarantee of human publication quality. Monitor committed chapters and final audit coverage. Download a recovery draft if time expires; label it as a draft, and retain the actual QA verdict.


## Qualify the exact 14-chapter planning workload first

For an autonomous planning-only probe, the same production story-bible,
outline batch, pacing validation and progression-review paths run in a separate
database. No chapters are drafted and no manuscript acceptance is claimed.

```cmd
docker compose -f docker-compose.yml -f docker-compose.local.yml exec -T web python -m novel_generator.services.local_verification --model gemma4:26b-a4b --base-url http://host.docker.internal:11434 --chapters 14 --words-per-chapter 3000 --context-tokens 32768 --outline-only --output-dir /app/artifacts/gemma26-outline-14
```

A successful report requires `outline_checks_passed: true`, a saved outline
with the requested chapter count, and a matching progression verdict.
`pipeline_complete` and `autonomous_checks_passed` remain false intentionally.
Use the usual two-chapter full-pipeline probe for prose and export checks.

To resume either new qualification type after an interruption, repeat the same
command and append `--resume`. The directory must contain its own manifest and
database, and all original options must match. A completed qualification is
reported without generating a new book; a failed or interrupted run resumes
saved checkpoints. If a provider cooldown is still active, the invocation
reports the queued state rather than bypassing the cooldown. An isolated
exclusive lock prevents two qualification processes from opening the same
database concurrently. Earlier probes without a manifest cannot be resumed by
this command. Keep the production worker stopped while benchmarking.


## Adaptive Ollama runtime

Production provider managers enable adaptive request sizing by default. Set
`OLLAMA_ADAPTIVE_RUNTIME=false` to restore fixed context behavior. The
configured `OLLAMA_NUM_CTX` (default 32768) is an administrator ceiling, not
a demand to allocate that context for every call. The controller consults
Ollama's model metadata, estimates prompt requirements conservatively, reserves
the existing stage output budget, and chooses a suitable context tier. It
keeps successful allocations per model during the provider client's lifetime
to avoid unnecessary model reloads. Explicit memory errors trigger bounded
context or processing-batch reductions, only when the estimated source and
output still fit. No automatic model switch, deletion, download, GPU clock
change, or modification to the Ollama server configuration is performed.

The configured ceiling is not a hardware benchmark, and the estimate is not
an exact tokenizer. Large prompts may be rejected conservatively. If the
necessary context cannot fit, the run surfaces an error rather than deliberately
truncating manuscript evidence. The diagnostics include the last successful
adaptive request's context, memory retries, throughput and (when available)
Ollama's reported resident allocation. These are historical model measurements,
not current free system RAM, free GPU memory, or temperature. Ollama owns the
hardware scheduler. Direct low-level clients remain fixed unless explicitly
enabled; the production ProviderManager enables adaptation automatically.
Older servers lacking metadata endpoints fall back to the configured ceiling
and request sizing. Actual GPU/CPU performance and memory pressure must be
qualified on the target machine.

References: https://docs.ollama.com/context-length ,
https://docs.ollama.com/api/ps ,
https://docs.ollama.com/api-reference/show-model-details .
