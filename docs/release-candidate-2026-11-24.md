# November 24, 2026 — local release-candidate runbook

**Pinned software candidate:** `9dc954b41e6d9deeeed5f452654220b846be001f` ([commit](https://github.com/ProhibitedTV/Novel-Generator/commit/9dc954b41e6d9deeeed5f452654220b846be001f)).
This includes the merged recovery, manuscript preservation, verified export, and simplified creation improvements through PR #151. It is a test candidate, not a qualified publishing engine. [Integrated CI](https://github.com/ProhibitedTV/Novel-Generator/actions/runs/45493576668) and [real-book qualification #129](https://github.com/ProhibitedTV/Novel-Generator/issues/129) are separate gates.

## 1. Prepare the existing installation

Save active work and stop the worker gracefully before changing versions. Back up the application database and artifact directory together, with both web and worker stopped. For Docker, preserve the existing data and artifact volumes; do not use `docker compose down -v`. Keep the existing environment file. Check `git status --short` and preserve local modifications before checking out a different revision.

```bash
git fetch origin
git checkout --detach 9dc954b41e6d9deeeed5f452654220b846be001f
```

For an existing native installation, activate its virtual environment and run:

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m novel_generator.services.longform_benchmark
ollama list
```

For an existing Compose installation using host Ollama, rebuild with `docker compose up -d --build`. Confirm the host Ollama address is correct in the existing environment; do not replace working settings blindly. The app and worker automatically migrate on startup. Check `docker compose ps` and `docker compose logs --tail=100 web worker`.

## 2. Test the ordinary web experience

Open http://localhost:8000 and verify the chosen local model in Provider Settings. Create a new book using a title, premise, target length and chapter count. Leave automatic chapter sizing enabled and choose **Create and generate book**.

For the initial smoke test, use 1,500 words and 3 chapters. Confirm the new run is autonomous, does not pause for outline approval, and exposes saved chapters while generating. On completion the current manuscript must pass checks and both exports must match the saved prose before the primary Word download appears.

Do not queue another job while the first is generating. A finished software smoke test is not evidence of full-novel quality.

## 3. Reproducible isolated real-model test

Alternatively, use the native CLI from the activated environment. Stop competing inference jobs first, including an active web worker. The CLI creates a separate database and manuscript directory; it does not modify your normal project database.

Replace `gemma4:latest` with the exact installed tag if needed. Record `ollama list` and `ollama show gemma4:latest` locally before starting; record the checkout SHA with `git rev-parse HEAD`. For a 12 GiB GPU, 32768 is the requested context ceiling, not a guarantee that every model fits entirely in VRAM. Monitor actual GPU and system memory, and use a compatible installed model. Avoid changing settings mid-qualification.

Short smoke:

```bash
python -m novel_generator.services.local_verification --model gemma4:latest --chapters 3 --words-per-chapter 500 --output-dir artifacts/rc-20261124-smoke
```

Full 60,000-word novel (new directory; may require multiple days):

```bash
python -m novel_generator.services.local_verification --model gemma4:latest --chapters 24 --words-per-chapter 2500 --output-dir artifacts/rc-20261124-full
```

Use a fresh output directory for each new experiment. If you need to resume after the process stops, rerun the identical command with `--resume` appended. The existing isolated database and configuration are reused. Record any manual intervention; a manually resumed run is recovery evidence, not an uninterrupted autonomous qualification. Temporary provider outages should instead be handled by the running process's persistent cooldown queue (default `PROVIDER_RECOVERY_ATTEMPTS=-1`). Do not intentionally interrupt the only long qualification just to test this: use a separate small test.

To inspect the saved progress of an isolated run from another terminal, use
the following command on a version that supports `--inspect` (added after
the pinned RC1 software commit):

```bash
python -m novel_generator.services.local_verification --inspect artifacts/rc-20261124-full
```

This reads the existing database in read-only mode, does not use Ollama or
acquire a worker lease, and prints a snapshot without replacing the final
report. Exit zero means inspection succeeded, not that the book passed.

## 4. Evaluate the result

Inspect `report.json` in the isolated output directory when the CLI settles. A successful autonomous report must contain:

- `pipeline_complete: true`, `autonomous_checks_passed: true`
- Exactly the expected consecutive chapters; chapter and total length checks true (54,000–66,000 words for the 60,000-word target).
- Current-source editorial coverage including chapter, identity, developmental, adjacent and distant pair reviews and whole-book review; no unresolved current-source concerns.
- Both Markdown and Word content matching the saved source.
- Recorded timings, retries, model identities and fallback events. Missing metrics must be documented as missing, not estimated.

Read the whole book independently. Check chronology, family roles, object ownership and destruction, causal transitions, character agency, dialogue, scene progression, repetition and the ending. Inspect the exported Word document and front matter. Record chapter and passage evidence for any defects. Preserve all failed editions, reports and databases locally; only share non-sensitive evidence publicly.

For the stronger release qualification, repeat against the saved Orchard brief and another substantially different premise as described in #129. Passing automatic checks does not establish publication readiness. Do not close #129 until the real-book criteria are satisfied.

## 5. Failure triage

- Provider unavailable: leave the active autonomous process running so its retry queue can recover, or inspect the model server and worker logs. Do not start another job against the same GPU.
- Stopped process: preserve the isolated directory; use the matching resume command if needed.
- Quality failure: preserve the source and review the failure/report rather than lowering acceptance thresholds.
- Missing or stale exports: preserve the database and artifacts; do not treat a terminal worker state as a finished book.
