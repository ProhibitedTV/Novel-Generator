# Book-wide acceptance and reconstruction

The October 14 editorial review of The Orchard of Borrowed Winters exposed real
misses in the prior acceptance contract. The saved Chapter 16 transfers and burns
the orchard deed; Chapter 24 pulls a final deed from a coat and destroys it again.
Chapter 16 also describes established sisters as sister and daughter. A completed
pipeline and 24 passing local chapter checks did not establish manuscript-wide
editorial correctness.

The strengthened autonomous gate adds:

- A stable-identity review of every complete chapter against author canon.
- A separate developmental review of every complete chapter with its assigned
  progression, complete book plan, and explicitly bounded source-derived openings
  and endings from the other chapters.
- Complete-source comparison for every non-adjacent chapter pair as well as every
  neighboring pair: 253 distant comparisons for a 24-chapter book, 276 pairs total.
- A separately configurable `autonomous_adjudication` route. This can use a
  different local model to verify proposed defects before edits. Existing routing
  falls back to the repair model when no separate verifier is configured.
- Source and context fingerprints, so an intervening chapter change invalidates
  affected distant comparisons. Changes to supporting passages trigger a new
  audit instead of applying stale diagnoses or terminating an otherwise valid
  repair sweep.

Acceptance contract 2 requires all these reviews, the whole-book review, and
existing word-length checks. Old contract-1 books are labeled with their narrower
coverage; missing new audits are not fabricated. A source-bound
`editorial_concerns_recorded` event after acceptance invalidates the current
acceptance indicator while preserving the completed text and prior reports.

Frequency, motifs, quiet aftermath, poetry, definitions, and independent
collection pieces are not automatic errors. A required developmental repair needs
actual evidence of material redundancy or broken progression, rather than a
reviewer's preferred style. Stable identity and pair audits have narrow contracts
so unrelated prose advice cannot masquerade as a factual contradiction.

## Starting a preserved-source revised edition

`POST /api/runs/{source_id}/reconstruct` accepts `revision_plan`, optional
`brief_updates`, and optional `task_routing`. It creates a separate project and
fresh autonomous run using the source's word/chapter budget. It does not copy
source prose, the failed outline, stale summaries, or old pass markers into the
new draft. The source remains linked and unchanged. Repeating the same request
reuses its revision run. The source hash and reconstruction plan remain a
first-class artifact after manuscript export.

The Orchard reconstruction plan preserves the premise and principals, delays the
final ownership transfer and shutdown until Chapter 21, gives Tessa consequential
agency, and assigns three distinct aftermath/closure chapters. Its technical
reference separates heat transfer, water allocation, stored-energy release, and
long-term soil recovery. The new writing uses local models; the plan is not
replacement prose.

Default Word exports now put each chapter on a new page and remove the blue accent
heading theme while retaining Heading 1 navigation. This is draft formatting,
not certification of a Kindle or print edition. Front matter, working ebook
navigation, trim layout, and preview qualification remain distinct production
work. See the official [ebook formatting guide](https://kdp.amazon.com/en_US/help/topic/G200910130)
and [Kindle content quality guide](https://kdp.amazon.com/en_US/help/topic/G200952510).


## Author-to-editor handoffs

Professional editing separates structural, stylistic, copyediting and proof
tasks (Editors Canada Professional Editorial Standards:
https://editors.ca/publications/professional-editorial-standards/). These are
distinct goals even when a single editor combines some passes.

The pipeline already performs chapter continuity checks, developmental
planning/revision, final line editing and current-source acceptance. It now
archives two named working editions: the complete first draft before
manuscript QA and structural revision, and the post-developmental draft before
line-level work. The downloads are checksum-verified and immutable once
registered; they never imply publication acceptance. Existing older runs
already past a milestone are not retroactively labeled as if their original
source were still available.

Summaries are a reverse-outline record of what the actual prose says, not
what the initial plan promised. Developmental prompts identify missing
evidence instead of assuming an outline event occurred. Compacted chapter
capsules are explicitly labeled as partial evidence rather than full source.
The actual source audits remain mandatory for autonomous acceptance.
Line editing should preserve voice and deliberate craft choices rather than
performing another structural rewrite.

The original developmental QA, revision plan and rendered editorial reports
are saved as a single checksummed agenda, with hashes identifying its source
chapter prose. On resume, the same assignment is used rather than making
the model re-diagnose partially edited prose and change its instructions.
The editorial tab shows the original chapter assignments and recorded
revision outcomes. The saved agenda is a historical editorial decision,
not proof that later edits resolved its findings. Fresh final QA and all
existing acceptance checks still run against current prose. Legacy runs
without a saved agenda establish one on their next developmental pass;
it describes the source at that time, not an invented original draft.

Next architectural milestones: a separate editorial letter, dependency-aware
revision ordering, a persisted fiction style sheet, and comparison of
line-edited versus structurally revised text for unintended changes.
