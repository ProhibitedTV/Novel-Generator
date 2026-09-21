# Unattended novel generation

Choose **Autonomous** on the project run form (the UI default), or send
`quality_profile: "autonomous"` when creating a run through the API. API callers
that omit the profile retain balanced behavior. Autonomous runs disable outline
approval and enable developmental rewriting automatically.

The workflow generates a story bible and validated plot, drafts each chapter,
and reviews its actual prose against the author brief, canon, prior story state,
adjacent chapters, and assigned ending. Nearby prose or repetition defects with
unique, contiguous quotations can be repaired within their complete paragraphs
(up to 250 words), preserving surrounding text exactly. Other problems trigger
a complete chapter revision. Both paths require another review before generation
continues. Reviews must cover all
requested chapters, explicitly answer every required check, and quote real prose
for each problem; missing checks and invented quotations cannot count as passes.
Omission markers in quotations are supported only when every retained excerpt
appears verbatim in the correct chapter and in order.

After the existing manuscript editing passes, the system reconstructs summaries
and continuity chronologically from the revised text. It reviews every final
chapter again and audits the book's structure, central promises, and ending.
Each chapter is checked in full; the whole-book audit uses a compact chapter map,
ending debt, and the full final chapter. Model context overflow stops the run
instead of silently discarding prose. Repairs invalidate affected review and
continuity caches. Final exports and completed status require all checks to pass,
chapter lengths to meet their ranges, and total length to fall within 10% of the
requested target. Impossible chapter-range/target combinations are rejected
before queueing.

No human approval or editing step is required in this workflow. Automatic checks
are still model judgments and cannot establish that every narrative defect has
been detected. Small local models may fail to produce an acceptable manuscript;
the software reports that failure rather than presenting it as a finished book.

## Local models and repair limits

Select an installed Ollama model for the run. Task routing exposes separate
**Automatic editorial review** and **Automatic editorial repair** stages, so a
different local model can review and revise the draft. Leave all stage providers
on Ollama to keep manuscript processing local. Configure sufficient context for
the full chapter plus canon and adjacent prose.

Two environment settings bound the extra work:

- `AUTONOMOUS_CHAPTER_REPAIR_ATTEMPTS=3`: repair attempts per chapter during drafting.
- `AUTONOMOUS_MANUSCRIPT_REPAIR_ROUNDS=3`: final repair sweeps and the maximum final repair attempts per chapter.

Both accept values from 1 through 6. Attempts are persisted before generation;
restarting or resuming does not erase the used budget. A final-stage checkpoint
resumes acceptance checks without repeating earlier drafting and editing. An unchanged or empty
revision stops with diagnostics. A provider interruption can be resumed using
the remaining budget. A chapter that exhausts its budget requires a new run or
an explicitly increased configured limit, ideally with a more capable local
model. Restart the worker after configuration changes.

If a whole-chapter revision still ignores a compression target, the editor
compresses ordered passages with measured word budgets. Each passage has at most
two attempts, and a chapter has at most 32 compression passages. Text is never
mechanically clipped to pass the count. The assembled chapter must pass fresh
continuity and editorial checks. Rejected review JSON is retained in diagnostics.

## Outputs and verification

Successful runs export Markdown, DOCX, QA, and an
`autonomous-quality-report.json` containing the manuscript hash, review history,
and repair attempts. Failed runs retain this diagnostic report and all saved
chapter work, without generating final manuscript exports. Previous chapter
versions live under the run's `editorial-history` artifact directory.

Run a real local smoke test in its own database:

```powershell
.\.venv\Scripts\python.exe -m novel_generator.services.local_verification --model gemma4:latest --profile autonomous
```

For a 60,000-word target, add `--chapters 24 --words-per-chapter 2500`.
This can take many hours. A short smoke test verifies execution of the workflow;
it does not qualify a model for an entire novel.
