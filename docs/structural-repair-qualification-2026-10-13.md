# Recovery from unchanged structural revisions

The Orchard of Borrowed Winters stopped during final editing on October 13.
All 24 drafts were preserved. Chapter 9 had 2,501 words, but its latter scene
began tunnel exploration and dismantling outside its assigned council-rejection
ending. The full-source chapter-9/10 audit diagnosed the chronology conflict.
Three successful Gemma 4 12B revision calls returned the unchanged chapter;
each completed with `done_reason: stop` and 3,219 evaluated output tokens.
Provider availability and response truncation were not the cause of this stop.

The repair now allows diagnosed erroneous events to change or disappear while
preserving correct events. After unsuccessful full rewrites, it switches to
explicit inclusive source paragraph ranges. A remaining structural diagnosis
after an earlier chapter rewrite uses this fallback directly, including after
resume. The application validates ranges and applies them atomically. Unselected
source text is retained exactly; invalid ranges, overlaps, unchanged candidates,
deletion of the entire chapter, and excessive growth cannot be saved.

## Native saved-failure qualification

The uninterrupted qualification used an isolated copy of the failed production
database with the production runtime, routes, and existing repair history.
There were no human prose edits, acceptance overrides, or budget resets.

| Check | Observed result |
| --- | --- |
| Structural repair, persisted final attempt 4 | Gemma 4 12B returned valid source-range edits on the first proposal. The erroneous final scene was removed, leaving 1,328 words. The first 40 source paragraphs were byte-for-byte unchanged. |
| Chapter and neighbor audits after removal | Chapter 9 and full chapter pairs 8/9 and 9/10 passed. The deterministic lower word bound remained blocking. |
| Length repair, persisted final attempt 5 | Automatic expansion and measured passage compression produced 2,490 words, within the configured 2,000–3,000 range. The chapter still ended after the council rejection rather than entering the tunnels. |
| Fresh audits of expanded prose | Chapter 9 and both complete neighboring pairs passed again. No required chapter-9 issue remained. |
| Source preservation | The other 23 chapters stayed unchanged. Production manuscript prose stayed unchanged throughout qualification. |
| Regression suite | 505 tests passed, including persisted strategy selection, atomic source-range edits, invalid-output rejection, unchanged-source retries, budget persistence, and structured truncation handling. |

Gemma 4 12B handled review and revision. Qwen 3.5 9B refreshed summaries and
continuity from the revised source. Ollama used a 32,768-token context and an
8,192-token output allowance. The source-range fallback inherited the configured
adjudication controls (`think=true`, temperature `1.0`); pair audits used the
same tested reasoning and temperature settings. An explicit repair-model route
was preserved.

The original chapter's application source hash was
`5b33bfe9c942f0b1b98ceaae677d682ce26999f631750173671b1721eedf90c4`.
The final qualified chapter's application source hash was
`2ffa39bed5fa57768cd53f7e47bcea7326620ff90b9877f1e2e474f0a098f184`.
Workstation evidence is retained under
`artifacts/structural-repair-recovery-20261013-v2/`, including the original
database backup, isolated database, repaired prose, and qualification report.

An earlier development probe changed wording while retaining the erroneous
scene. It was stopped and preserved separately; it is not counted as this
uninterrupted qualification. This motivated persisted strategy selection rather
than treating any changed wording as proof that a structural repair succeeded.

This qualifies recovery of the saved failure. It does not certify the entire
novel. Deployment resumes the original run through its normal complete metadata,
chapter, pair, manuscript, length, and export gates. All remaining defects still
prevent manuscript acceptance.
