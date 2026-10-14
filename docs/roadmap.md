# Roadmap and acceptance evidence

The first priority is a coherent complete manuscript generated with local models.
Illustrations follow text acceptance. Completing individual chapters or passing
model controls does not establish that a novel is publishable.

## Manuscript reliability

| Work | State | Required evidence |
| --- | --- | --- |
| Persistent chapter recovery, source-grounded editorial checks, automatic editing, and complete exports | Implemented | Continue regression checks and independent inspection of generated books. |
| Fresh unattended local pipeline | Qualified for one small custom-fiction sample | The Apricot Boundary completed at 1,375 words on October 11, with matching saved prose and Markdown/Word exports. See [model qualification](current-local-models.md). |
| Whole-book progression across outline batches and continuations, [#75](https://github.com/ProhibitedTV/Novel-Generator/issues/75) | Implemented; live controls and 24-chapter planning qualification passed | Detected the saved repeated resolutions, accepted staged agreements and independent collections, and generated a reviewed replacement plan with an unchanged completed prefix. See [qualification evidence](whole-outline-qualification-2026-10-11.md). |
| Novel-length completion | One local novel completed and automatically accepted | The Orchard of Borrowed Winters completed on October 13: 24 chapters, 59,604 words, all 24 final chapter audits, 23 full neighboring-pair audits, and the whole-book review passed. The acceptance fingerprint matches saved prose; Markdown and Word exports contain all 24 accepted chapters. See [final QA coverage](final-qa-coverage.md). This establishes pipeline completion for this book; independent literary assessment and multi-genre qualification remain outstanding. |
| Literary quality and variety | Needs broader qualification | Inspect character agency, dialogue and voice variation, scene variety, repetition, pacing, and causal consequences in actual completed prose. Test more than one genre and premise with fresh databases and no human prose edits. |
| Source references and automatic recovery in pair audits | Implemented; failed pair and seven live controls qualified | The original chapter-16/17 contradiction remained blocking; six automatic repair rounds passed the full pair. Final checks accepted coherent travel, corrected beliefs, and document aliases while retaining genuine contradictions. See [local qualification](pair-editorial-qualification-2026-10-12.md). All 23 neighboring pairs subsequently passed for the completed novel. |
| Recovery when structural revisions copy source | Implemented; saved chapter-9 failure qualified | Two automatic repair rounds removed the premature scene and restored chapter length. The resulting 2,490-word chapter and both complete neighboring pairs passed fresh local-model reviews. Other chapters and production prose remained untouched during qualification. See [October 13 evidence](structural-repair-qualification-2026-10-13.md). |
| General book forms | Implemented with limited live evidence | Custom fiction has a fresh small-book completion; the source-aware handbook sample used development checkpoint resumes. Collections and other forms still need their own full pipeline qualifications. |

The global planner and review distinguish repeated endings from legitimate
aftermath, staged decisions, motifs, and independent collection pieces. An
unsupported review cannot require a rewrite. An unresolved required defect
cannot be turned into a completion flag by increasing a budget or skipping a check.

## Outstanding illustration issues

The image settings and text-only metadata foundations are implemented. Continue
with rendering and its outputs in this dependency order:

1. [#38: Local image generation settings](https://github.com/ProhibitedTV/Novel-Generator/issues/38) and [#39: Extract chapter IMAGE PACK metadata](https://github.com/ProhibitedTV/Novel-Generator/issues/39): implemented with disabled defaults, explicit connection testing, source-bound metadata, downloadable extraction evidence, and warnings for absent/malformed packs. See [setup and qualification](local-images.md).
2. [#40: Post-text image rendering](https://github.com/ProhibitedTV/Novel-Generator/issues/40) and [#41: Rendered image artifacts](https://github.com/ProhibitedTV/Novel-Generator/issues/41). Require persisted per-image recovery, explicit timeouts, and accepted manuscript input.
3. [#42: Image manifest export](https://github.com/ProhibitedTV/Novel-Generator/issues/42) and [#43: Run image gallery](https://github.com/ProhibitedTV/Novel-Generator/issues/43). Export references must resolve to saved artifacts, and the gallery must distinguish pending, failed, and completed images.

[#44: Cover prompt pack](https://github.com/ProhibitedTV/Novel-Generator/issues/44)
is implemented independently of rendering. The local qualification produced
three source-grounded concepts for the accepted small book and reused the pack
without changing prose or text exports. See [setup and qualification](cover-prompts.md).

An image-provider failure must preserve the accepted text and completed exports.
Rendering-specific implementation and integration checks remain outstanding;
settings and extraction tests do not establish image-generation readiness.
