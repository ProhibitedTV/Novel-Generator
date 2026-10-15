# Model changes and reusable review evidence

Ollama model tags are mutable. Editorial and whole-outline review caches now
resolve the current installed manifest digest through
[`GET /api/tags`](https://docs.ollama.com/api/tags), together with the runtime
version and default context, output, thinking, and structured-temperature settings.
Reviewer and verifier identities contribute to the source-bound fingerprint and
are retained in review events.

Unchanged model identities can reuse unchanged source reviews. Changing either
model's digest, the runtime version, or generation defaults invalidates the prior
check even when its model name stays the same. Identity is checked again before a
new verdict is accepted. A mid-review change discards that verdict and enters the
existing persisted provider retry path; it never authorizes a prose repair.

Standard providers with unverifiable revisions cannot reuse these review caches.
An OpenAI-compatible model ID alone is not treated as an immutable weight revision.
Legacy/custom adapters without a metadata resolver retain their existing cache
contract; they do not supply verified model-revision evidence.

The whole-outline reviewer also uses the configured `autonomous_adjudication`
route to verify proposed defects. If it is absent, the existing
`autonomous_revision` fallback remains. A larger reviewer can therefore be tested
with a separate verification model without changing the drafting model.

`autonomous_book_review` optionally selects a separate model for the final
whole-book review. Ordinary chapter, identity, developmental, and pair audits
continue using `autonomous_review`; an unset book route preserves that behavior.
Whole-book defects are independently verified before their first repair. Project
create/edit forms now persist both dedicated roles, alongside the existing editor
roles. This permits a slower CPU-offloaded final reviewer without imposing that
cost on routine generation. Every role can use an available configured provider.
Selecting this role changes the reviewer, not the amount of source text supplied.
The whole-book review uses summaries and opening/ending excerpts for earlier
chapters and complete final-chapter prose. Full-source chapter and chapter-pair
audits are separate gates; a whole-book pass alone is not full-source coverage.

Regression coverage includes mutable aliases, runtime/default changes, missing
metadata, exact-identity reuse, reviewer/verifier changes, unavailable metadata,
mid-review changes with unchanged prose and no acceptance event, and independent
outline routing and persistence of both dedicated roles through project forms.

## Local model qualification, October 14, 2026

On this workstation (RTX 3060 12 GB, 32 GB RAM, Ollama 0.40.3),
`gemma4:12b` and `gemma4:26b-a4b-it-qat` each passed the same four isolated
production-review controls: an explicitly prohibited early resolution, a family
identity contradiction, a deed contradiction, and a coherent distinct-document
sequence. Both used independent `qwen3.5:9b` adjudication. Aggregate elapsed time
was approximately 14.2 minutes for 12B and 20.2 minutes for 26B. These small
controls establish neither superior literary judgment nor publication readiness.

The 26B reviewer also passed the known-flawed original manuscript when supplied
the compressed whole-book context. That negative result prevents treating a
larger-model swap as a fix for source coverage. A complete-source, 131,072-token
probe was prepared but has not been executed or qualified on this hardware.
The production routes remain unchanged while novel generation continues.
