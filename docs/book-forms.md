# Genres and book forms

Choose **Custom / blended genre** to describe any genre, subgenre, topic, or blend. Add an audience, reader promise, and explicit genre/form requirements. The built-in genre profiles remain available as templates; unfamiliar profile names no longer silently display a sci-fi thriller profile. Existing saved novels retain their original prompts and checkpoints.

The book brief separates three decisions:

- **Book type** is free text: novel, novella, anthology, memoir, handbook, sonnet sequence, radio play, or another form.
- **Content** selects fiction, nonfiction, poetry, or drama. It changes drafting and acceptance instructions, including the meaning of completion.
- **Structure** selects a continuous book, episodes, independent pieces, a learning sequence, reference sections, or an author-defined structure. Independent pieces do not acquire a compulsory shared plot or character roster.

These contracts apply to planning, outline recovery, chapter planning, drafting, revisions, summary/continuity refresh, whole-book QA, final editing, and autonomous acceptance. The existing checkpoint schemas keep their names: for example, a handbook's `act_plan` describes its organization and `character_turn` can describe reader insight. It does not need an invented protagonist or a midpoint reversal. Poetry may use deliberate fragments and refrains; drama preserves speaker attribution and the requested script format. Word targets and complete chapter coverage still apply to every form. Inapplicable numeric dialogue/agency scores remain visible; they do not automatically create fictional repair requirements.

Each generated bible saves the book specification. Later project edits do not silently change its stored form or source contract. New outline requests constrain the requested chapter range in their provider schema. Reviews constrain `reviewed_chapters` to their complete requested coverage and retain strict validation of actual manuscript evidence. Known defects remain blocking, including factual defects in nonfiction.

For a non-final chapter, completion means delivering that chapter's assigned purpose and stopping state; the unwritten book ending is not a defect in its setup. Invalid review retries retain the entire source and contract but do not replay the invalid answer as an assistant response. This prevents an unsupported failed flag from becoming the template for later verdicts or promoting optional wording advice into a mandatory repair. Final reviews still require the actual promised ending, and all required diagnoses retain source-evidence validation.

Word exports preserve section headings, emphasis, ordered and bulleted lists, reference tables, code, quotations, and line breaks within poems and scripts. Publication Markdown preserves its original structure. Source links retain their destinations in Word text; remote assets are not fetched. These are editable book exports, not automatic qualification of every publisher's typography requirements.

Automatic passage edits, coordinated edits, and full chapter rewrites all use the saved form and source contract. Reviewers distinguish localized defects from missing sections or major structural outcomes. A source paragraph anchors the evidence; a structural diagnosis requests a complete chapter rewrite rather than confining missing coverage to that paragraph. Coordinated edit slots include their original paragraph numbers and preserve each passage's subject and unique supported information. Repair histories and limits remain persisted, and every candidate still requires fresh acceptance checks.

Reviews receive computed matches for long copied paragraphs within a chapter and between distant chapters, with their original source locations. These are evidence to evaluate, not automatic failures: a refrain or intentionally repeated reference instruction can be valid. Accidental duplicated definitions, explanations, scenes, or examples still require correction.

Continuity audits distinguish established facts from a character's speech, beliefs, guesses, lies, and later discoveries. Disagreement or a corrected belief does not automatically contradict the book. Every proposed cross-chapter continuity defect receives a separate verification pass against the complete source before the first repair; confirmed incompatible facts remain blocking. This adds inference time in exchange for checking an allegation before it can change the manuscript.

## Nonfiction sources

Paste source **content**, with source names or citations, into the source-material field. A URL alone is not retrieved and is rejected. Nonfiction requires this field before a project is saved. Prompts prohibit invented research, quotations, statistics, credentials, and historical claims, and distinguish hypothetical examples from sourced assertions. In autonomous mode, a separate focused source audit compares every complete chapter with the supplied material, before accepting a draft and again after final editing. Unsupported assertions and invented mandatory procedure steps require repair even if the broad editorial review passes. This is source-aware generation, not a research crawler or an independent fact-verification service.

Book-form support does not establish publication quality for every genre, topic, language, or age group. Local model quality and context limits remain material; inspect the qualification report and resulting text. Long novels still need full-length completion evidence, beyond short examples.

## Test a local model combination

The qualification command creates a **fresh isolated database** and runs the same worker pipeline, including autonomous gates and Markdown/Word exports. On a single GPU, run it sequentially with the production worker. It never joins a native worker to the production database.

```powershell
python -m novel_generator.services.local_verification `
  --model qwen3.5:9b --review-model gemma4:12b --revision-model gemma4:12b `
  --project-file examples/qualification/nonfiction-handbook.json `
  --output-dir artifacts/my-handbook-qualification
```

Use `examples/qualification/custom-fiction.json` for a quiet literary-western example. These files are reusable test briefs, not prewritten manuscripts. The handbook source defines an original proposed procedure, not a claim about an existing organization. Other installed models can be supplied through the same arguments. An autonomous qualification returns success only when the pipeline finishes and acceptance matches the actual final manuscript.

The create/edit forms also persist the automatic reviewer, automatic repair, and developmental-rewrite model choices. They previously displayed those controls but discarded the submitted values. No specific model family is required by the book-form contracts.
