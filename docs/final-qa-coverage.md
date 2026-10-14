# Final editorial coverage

The autonomous run UI counts final source-bound reviews, separately from historical
draft scorecards. Repairs correctly clear `chapter.qa_notes` when a draft changes.
Counting those surviving scorecards as final QA previously displayed **1 / 24** for
The Orchard of Borrowed Winters despite all final audits being present.

For run `263410f4-1dfe-4a69-be1b-054ee31f7225`, the October 13 accepted snapshot has
24 chapters and 59,604 words. Saved review events show 24 passing final chapter
audits, 23 passing complete neighboring-pair continuity audits, and one passing
whole-book review. Its manuscript fingerprint is
`679513fd5d2e7324ecc9596230cb8c5fe9f2d56fd058ae17335727679ccb3995`.
Both the saved acceptance event and final report match that fingerprint.

New review events record individual chapter source hashes. Editing prose invalidates
affected chapter, pair, and whole-book coverage. Legacy events without these hashes
are counted only within a complete acceptance snapshot matching the current
manuscript. A completed status, report file, or acceptance marker alone cannot
fabricate missing audits. Failed final audits replace earlier passes for the same
source and scope. The run page exposes checks for each chapter and links the full
editorial report without manufacturing numeric draft scores.
The dashboard also separates current final review results from collapsed historical
draft scores and planning warnings. Earlier revision triggers remain available for
comparison and are not presented as outstanding final repair instructions.

These are automated editorial checks. Whole-book review uses bounded excerpts,
chapter summaries, and the ending, alongside the separate full chapter and pair
reviews. Completion and source integrity do not certify literary quality or
replace independent assessment of voice, pacing, originality, and reader experience.
