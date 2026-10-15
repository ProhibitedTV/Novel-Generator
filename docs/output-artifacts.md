# Unintended output artifacts

Autonomous draft and final-manuscript gates now inspect suspected chapter-summary,
production-note, placeholder, and assistant-commentary language using a separate
`output_artifacts` review. The detector selects a review; it never deletes or
rewrites matching text. It recognizes selected chapter-ending constructions in
present and past tense, chapter/scene planning descriptions, assistant identity
phrases, and insertion instructions. It is not an exhaustive detector of all
possible output artifacts.

The reviewer receives the complete numbered chapter and author book contract.
It must distinguish accidental production text from intentional metafiction,
narrator address, quoted dialogue, and nonfiction chapter discussion. Only
source-grounded prose defects are valid in this scope. Every proposed defect
passes the dedicated independent adjudication route before the existing targeted
repair loop may change prose. Both model revisions and the complete source/form
context bind persisted evidence. A changed chapter is checked again; final review
also checks chapters completed before this safeguard was deployed.

Synthetic tests cover confirmed and rejected allegations, unchanged-source cache
reuse, legitimate book-form language, source preservation, and both draft and
final repair/recheck paths. Actual manuscript qualification stays in local
artifacts; repository tests contain synthetic text only. This safeguard does not
certify general literary quality or publication readiness.

On October 15, 2026, local `gemma4:12b` review with `qwen3.5:9b`
independent adjudication blocked the saved test chapter's unintended synopsis
and accepted both intentional metafiction and nonfiction instruction controls.
Adjudication rejected a false allegation against the nonfiction control; its
matching wording alone did not authorize a rewrite. All three verdicts matched
the expected outcomes. This narrow qualification is not an evaluation across
all genres or artifact patterns. Private source and detailed results stay local.
