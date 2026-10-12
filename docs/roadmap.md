# Roadmap and acceptance evidence

The first priority is a coherent complete manuscript generated with local models.
Illustrations follow text acceptance. Completing individual chapters or passing
model controls does not establish that a novel is publishable.

## Manuscript reliability

| Work | State | Required evidence |
| --- | --- | --- |
| Persistent chapter recovery, source-grounded editorial checks, automatic editing, and complete exports | Implemented | Continue regression checks and independent inspection of generated books. |
| Fresh unattended local pipeline | Qualified for one small custom-fiction sample | The Apricot Boundary completed at 1,375 words on October 11, with matching saved prose and Markdown/Word exports. See [model qualification](current-local-models.md). |
| Whole-book progression across outline batches and continuations, [#75](https://github.com/ProhibitedTV/Novel-Generator/issues/75) | Implemented; novel-length planning qualification in progress | Detect the saved repeated resolutions, accept staged agreements and independent collections, and generate a coherent 24-chapter replacement plan with an unchanged completed prefix. |
| Novel-length completion | In progress; not qualified | Finish The Orchard of Borrowed Winters, then check all final chapters, continuity, target length, author promises, ending, and export integrity. The earlier outline repeated treaty ratification and first orchard planting at chapters 8, 16, and 24; its draft remains preserved. |
| Literary quality and variety | Needs broader qualification | Inspect character agency, dialogue and voice variation, scene variety, repetition, pacing, and causal consequences in actual completed prose. Test more than one genre and premise with fresh databases and no human prose edits. |
| General book forms | Implemented with limited live evidence | Custom fiction has a fresh small-book completion; the source-aware handbook sample used development checkpoint resumes. Collections and other forms still need their own full pipeline qualifications. |

The global planner and review distinguish repeated endings from legitimate
aftermath, staged decisions, motifs, and independent collection pieces. An
unsupported review cannot require a rewrite. An unresolved required defect
cannot be turned into a completion flag by increasing a budget or skipping a check.

## Outstanding illustration issues

The seven illustration issues remain open. Implement them in this dependency order:

1. [#38: Local image generation settings](https://github.com/ProhibitedTV/Novel-Generator/issues/38), then [#39: Extract chapter IMAGE PACK metadata](https://github.com/ProhibitedTV/Novel-Generator/issues/39).
2. [#40: Post-text image rendering](https://github.com/ProhibitedTV/Novel-Generator/issues/40) and [#41: Rendered image artifacts](https://github.com/ProhibitedTV/Novel-Generator/issues/41). Require persisted per-image recovery, explicit timeouts, and accepted manuscript input.
3. [#42: Image manifest export](https://github.com/ProhibitedTV/Novel-Generator/issues/42) and [#43: Run image gallery](https://github.com/ProhibitedTV/Novel-Generator/issues/43). Export references must resolve to saved artifacts, and the gallery must distinguish pending, failed, and completed images.
4. [#44: Cover prompt pack](https://github.com/ProhibitedTV/Novel-Generator/issues/44). Derive at least three distinct concepts from the final manuscript, with title-safe composition and the requested aspect and negative prompts.

An image-provider failure must preserve the accepted text and completed exports.
These issue-specific implementation and integration checks remain outstanding;
the roadmap does not count documentation as feature completion.
