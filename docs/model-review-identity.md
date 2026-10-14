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

Regression coverage includes mutable aliases, runtime/default changes, missing
metadata, exact-identity reuse, reviewer/verifier changes, unavailable metadata,
mid-review changes with unchanged prose and no acceptance event, and independent
outline routing. The full local suite passed 581 tests during the October 14 audit.
