# Current local-model qualification

On the audited RTX 3060 (12 GB VRAM) and 32 GB RAM workstation, the installed Ollama 0.40.2 runtime matches the [official current release](https://github.com/ollama/ollama/releases/tag/v0.40.2). Model tags still need separate checking: the July `gemma4:latest` manifest differed from the registry, while `qwen2.5:7b` matched its older model-family tag.

The October 10, 2026 audit installed [Qwen 3.5 9B](https://ollama.com/library/qwen3.5:9b), [Gemma 4 12B](https://ollama.com/library/gemma4:12b), and refreshed `gemma4:latest`. It retained the previous Gemma tag as `gemma4:archive-20260708` for comparison. Explicit size tags help avoid accidentally choosing a larger variant when `latest` changes.

Both candidate models were tested on a coherent non-final setup, a real paper-ledger contradiction, and the saved Chapter 2. These probes distinguish scope judgment and contradiction detection from novel completion. Their raw responses and the before/after model digests are saved in the workstation's ignored `artifacts/model-audit-20261010/` directory.

Thinking-capable models may use their response allowance on reasoning before emitting any final answer. A live Qwen probe exhausted 64 tokens with an empty answer under its default thinking mode, while `think=false` returned the requested JSON in seven tokens. The application therefore defaults to `OLLAMA_THINK=false`; `OLLAMA_THINK=true` enables thinking explicitly. Reasoning text is separate from manuscript text. See [Ollama thinking controls](https://docs.ollama.com/capabilities/thinking).

For a fresh isolated pipeline test:

```powershell
ollama pull qwen3.5:9b
ollama pull gemma4:12b
docker compose -f docker-compose.yml -f docker-compose.local.yml exec -T worker python -m novel_generator.services.local_verification --model qwen3.5:9b --review-model qwen3.5:9b --revision-model qwen3.5:9b --base-url http://host.docker.internal:11434 --chapters 2 --words-per-chapter 700 --output-dir /app/artifacts/current-model-e2e
```

Use a new output directory for each qualification. Check that `report.json` contains both `pipeline_complete: true` and `autonomous_checks_passed: true`, inspect the manuscript and final ending, and verify the exported Markdown and DOCX files. A miniature manuscript confirms the pipeline for that sample; a whole novel still needs its own final checks and exports. Use `--project-file` with a ProjectCreate JSON to test other premises, genres, and novel-length targets.

Provider Settings are persisted in the database. Update the default model and the run's stage routes there as well as any environment defaults. Keep the Ollama URL at `http://host.docker.internal:11434` for Docker deployment. A model update alone does not erase previous repair history or restart a failed run.
