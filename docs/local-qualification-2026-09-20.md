# Local qualification: September 20–21, 2026

## Verified scope

Upstream was fetched and the checkout fast-forwarded from `49402cf` to `3353950`.
Existing uncommitted UI, story-brief, prompt, and editorial changes were preserved.
The additional fixes described below remain local working-tree changes.

The Windows installation uses Python 3.12.14 in `.venv`, native Ollama at
`http://127.0.0.1:11434`, and SQLite. Docker was unavailable. The previous Docker
`.env` was backed up to the ignored `artifacts/setup-backup/docker.env`; the active
configuration uses `data/novel_generator.db` and `artifacts` inside the repository.
Both installed models were exercised: `qwen2.5:7b` and `gemma4:latest`.

## Fixes driven by verification

- First startup now creates the SQLite parent directory before Alembic runs.
- Short outlines use record validation followed by the existing whole-book pacing
  repair, preserving the generated plot when a midpoint label fails validation.
  The live baseline had discarded the entire plot and substituted generic
  "Pressure Point" chapters for that exact error.
- Missing live continuity fields preserve existing state. Explicit empty fields
  still resolve debt. Chapter and editorial checkpoints retain this distinction
  on replay, and Ollama's continuity schema requests explicit live-state fields.
- Known cast members' given names and surnames no longer trigger new-entity
  warnings, while unknown names remain detectable.
- Chapter prompts end with a bounded reminder of the current stopping state,
  later objectives, and author-specified character facts and world rules. This
  addresses observed premature finale narration and occupational drift. It is a
  prompt safeguard, not a semantic guarantee.
- `python -m novel_generator.services.local_verification` provides an isolated
  real-model check using the worker's complete runtime stack, with a saved
  database, manuscript exports, QA, provider telemetry, and completion report.

## Results

- Full automated suite after the September 21 fixes: **284 passed**. Existing deprecation warnings remain.
- Deterministic 32-chapter long-form benchmark: **9/9 passed**. This tests memory,
  callbacks, pacing, ending convergence, and QA coverage without model inference.
- Qwen baseline: **3 chapters, 2,266 words**, manuscript and QA exports completed;
  the outline fell back after a midpoint validation failure.
- Qwen repeat after outline/continuity fixes: **3 chapters, 2,902 words**, all
  checkpoints and Markdown/DOCX/QA exports completed, **zero failed attempts and
  zero fallback events**. The model's actual outline was preserved.
- Subsequent chapter-boundary probes on the final prompt changes: Qwen produced
  **524 words**, and Gemma produced **728 words**. Both deferred the hearing and
  job-loss finale. Qwen still showed a dialogue-attribution error. A manual read
  of the Gemma sample found no corresponding role or attribution error.
- Gemma's single request took about **118 seconds**, including approximately
  **50 seconds loading** and **41 seconds prompt evaluation**. A single chapter
  is insufficient evidence to replace the end-to-end-tested default model.
- Local web and worker services were started with the latest code. Database and
  Ollama health checks passed on port 8000. Runtime logs are under `logs`.

The repeat Qwen run started before the later boundary and name-lint refinements;
those changes were verified by regression tests and focused live chapter probes,
not by another complete manuscript run.

## Evidence and limits

Local evidence is intentionally ignored by Git:

- `artifacts/local-verification-qwen/report.json`
- `artifacts/local-verification-qwen-fixed/report.json`
- Each verification directory's `manuscript/<run-id>/` contains the manuscript
  and final QA report.
- `artifacts/chapter-boundary-probe.md`
- `artifacts/chapter-boundary-probe-gemma.md`

Both Qwen manuscript QA reports request editorial revision. Observed weaknesses
include premature payoffs, repeated turns, abstract endings, and occasional
character/attribution drift. Generated word counts also exceeded the small
smoke-test targets. These results prove execution and export at smoke-test scale;
they do **not** qualify a logically consistent 60,000-word novel or publication
readiness. The synthetic 32-chapter benchmark does not close that gap.

The next novel-scale qualification gate is a full-length autonomous run with
automatic checks against the original brief, chapter chronology, clue/payoff
locations, character facts, ending closure, and actual word count. For example:

```powershell
.\.venv\Scripts\python.exe -m novel_generator.services.local_verification --model gemma4:latest --chapters 24 --words-per-chapter 2500 --profile autonomous
```

This can take hours. An autonomous run also requires automatic editorial
acceptance before completion; those model judgments still do not prove that
every narrative defect has been found. The installed model defaults were not
changed based on one Gemma chapter. No cloud inference provider was used.

## Autonomous editing verification

The new profile requires grounded chapter reviews, bounded revisions, fresh
continuity after edits, final chapter and book audits, and deterministic word
limits. It does not pause for human approval. Invalid reviews and exhausted
repairs preserve work and diagnostics without final manuscript exports.
See [Unattended novel generation](autonomous-editing.md).

A real one-chapter Gemma run (`7d9c4de6-cd7c-4ff0-9bef-fdf763bccea2`)
exposed invented review quotations and repeated optional stylistic suggestions.
It stopped rather than exporting an accepted manuscript. Review retries now
retain source prose, failed ending checks require a relevant ending issue, and
final-chapter instructions explicitly require the author's resolution rather
than setup for another chapter. A subsequent local reviewer probe correctly
identified the missing hearing, proof of fraud, and aftermath. Saved evidence is
under `artifacts/autonomous-verification-gemma`, including review and repair
history. These are short workflow checks, not novel-length qualification.

## September 21 follow-up

Upstream was fetched again; `HEAD` and `origin/main` both remained at `3353950`.
The web service and worker were restarted with the local changes, and the web
page returned HTTP 200. No changes have been committed or published.

Live repair testing exposed duplicate prose caused by compression prompts that
included neighboring passages. Those prompts now contain only the passage being
compressed. Usable short revisions are retained for the outer word-count gate
instead of discarded. Whole-book review context also avoids presenting the final
chapter's prose twice.

The editor now supports evidence-anchored paragraph repairs for nearby prose and
repetition issues. Exact unique source anchors and a 250-word passage limit bound
these edits; other diagnoses retain whole-chapter revision. Both paths invalidate
metadata and require fresh reviews under the same persisted repair budget.

A real `qwen2.5:7b` passage probe consolidated the duplicated Thorne speech in a
61-word passage. The resulting manuscript went from 758 to 733 words, with the
prefix and suffix preserved exactly. This probe alone is not manuscript
acceptance. Evidence: `artifacts/autonomous-verification-gemma/local-passage-probe.json`
and `local-passage-probe.md` in the same directory.

Earlier final-stage runs exhausted their budgets on length, repetition, and
ending defects; final manuscript exports were withheld. Derivative runs used
saved prose and explicit source-run lineage, so they must not be described as
fresh end-to-end drafting or novel-scale qualification.

The derivative run `2e0355b0-f311-4ad4-8892-ab1745c85c04` ended at 720 words
after three repairs. Its final reviews still flagged repeated emotional themes
and an unnamed official's authority to issue the legal resolution. It failed
the acceptance gate and withheld final exports. The report is
`artifacts/autonomous-verification-gemma/report-local-repair.json`.
This test exercised Gemma review and Qwen revision, including a localized final
paragraph edit; it does not establish publication readiness.
