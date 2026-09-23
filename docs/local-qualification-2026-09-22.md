# Sustained Ollama revision qualification: September 22, 2026

## Implemented behavior

- Draft repair budgets default to 12 attempts per chapter; final revision allows
  24 rounds and attempts per chapter. Both are configurable up to 100. Existing
  persisted attempts count after a restart or an increased limit.
- Structural issues are addressed before length and prose polish. Deferred
  diagnoses remain recorded and all acceptance checks still run on revised text.
- Separate local defects can be repaired in up to eight passages per attempt.
  Overlapping diagnoses are combined; saved prose changes only after all passage
  calls succeed. A return to an earlier rejected version stops with diagnostics.
- Review evidence permits dialogue quotation-mark formatting differences without
  allowing changed words or invented facts. Reviewer instructions distinguish
  missing causes/payoffs from optional dramatic emphasis.
- The isolated verification CLI accepts any project JSON and separate Ollama
  review/revision models. Fantasy, romance, and horror example briefs each target
  24 chapters and 60,000 words. Their schemas and feasible lengths were checked;
  they have not yet passed full-length generation.

## Live result

The complete automated suite passed: **292 tests**, with existing deprecation
warnings. The working diff also passed whitespace checks.

The previously failed run `2e0355b0-f311-4ad4-8892-ab1745c85c04` was resumed from
its saved final-stage checkpoint with its repair history intact. An initial
attempt exposed a dialogue quotation-format validation failure; after fixing
that validator and refining the review instructions, the resumed run completed
at **680 words** against a 700-word target. Its final chapter and whole-book
reviews passed, and Markdown/DOCX/QA exports were produced. No saved manuscript
prose was manually edited to obtain that result.

This used local `gemma4:latest` review and `qwen2.5:7b` revision. It is a resumed
short-manuscript workflow check, not a fresh novel, a genre benchmark, or proof
that model judgments detect every defect. More time and a passing automated
report cannot alone establish publication quality.

Evidence remains in ignored local artifacts:

- `artifacts/autonomous-verification-gemma/report-sustained-revision.json`
- `artifacts/autonomous-verification-gemma/manuscript/2e0355b0-f311-4ad4-8892-ab1745c85c04/`

The web application and worker were restarted with the changes; the web endpoint
returned HTTP 200. These follow-up changes are local and have not been published.

## Full-length run started

`The Orchard of Borrowed Winters` was queued in the normal application database
as run `202cd476-7488-4f2a-9a81-68884da7205a`, targeting 24 chapters and 60,000 words.
Gemma handles drafting and review; Qwen handles automatic revision. The run uses
12 draft attempts per chapter and 24 final rounds. Its progress is available at
`http://127.0.0.1:8000/runs/202cd476-7488-4f2a-9a81-68884da7205a`.
It is an ongoing qualification run, not a completed or accepted novel.

## Outline recovery follow-up

The full-length run subsequently failed because its 24-chapter outline response
hit the model output limit; the repair response also ended at the output limit.
No prose chapters were generated. The worker preserved the accepted story bible.

Autonomous outline planning now requests at most four chapters per batch, saves
each validated result, and recursively subdivides invalid batches down to one
chapter. Saved split decisions and accepted batches survive resume; fingerprints
include the brief, bible, route, word targets, and prior accepted outline. Whole
book validation remains required before drafting. JSON repair prompts retain
the original planning context, and failures expose the underlying diagnostic.
UI call estimates reflect the smaller batches.

Regression tests cover partial-batch recovery without repeating accepted work,
checkpoint invalidation after a changed brief, bounded failure of a single
chapter, and preservation of planning context during JSON repair. The same novel
run was resumed from its saved story bible with the updated worker.

The full suite passed **295 tests** after this fix. In the live resumed run,
Gemma produced and validated chapters 1–4 in approximately 74 seconds; their
checkpoint was saved and the worker advanced to chapters 5–8. This verifies the
first batch, not completion of the full outline or manuscript.

## September 23 reliability follow-up

The live run completed all 24 outline entries, then reached chapter 1 editorial
repair. Its reviewer incorrectly requested work in later chapters and rejected
an otherwise grounded quotation with a two-word opening excerpt before an
omission marker. Chapter reviews now explicitly enforce the assigned stopping
state, reject instructions directed at a future chapter, and allow short excerpt
segments only when the combined citation is sufficiently substantive and every
retained segment matches the source. Review correction is bounded at three
attempts with source prose retained.

Temporary provider transport failures now schedule persisted, cancelable
cooldowns rather than immediately requiring manual resume. Delays grow from
30 seconds to 15 minutes, with a default limit of 96 recoveries per run. Other
queued jobs remain eligible while a run waits. Missing models and editorial
failures do not receive transport retries. Interrupted, unsaved editorial repairs
are recorded separately and do not spend the editorial attempt budget.
