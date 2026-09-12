# Long-form benchmark and generation telemetry

Novel Generator includes a deterministic stress benchmark for the long-form context architecture. It does not call an LLM. Instead, it constructs a deliberately difficult synthetic 32-chapter manuscript and verifies that the deterministic systems still retrieve, bound, prioritize, converge, and diagnose the correct state as the book grows.

Run it locally with:

```bash
python -m novel_generator.services.longform_benchmark
```

The command prints a JSON report and exits non-zero if any benchmark check fails.

## What the benchmark stresses

The synthetic book contains:

- an object/clue planted in chapter 4 that becomes relevant again much later;
- a supporting character who disappears while an unresolved trust fracture remains active;
- a large continuity ledger with long timeline/entity/system history;
- enough under-writing to put the manuscript behind its whole-book word target;
- unresolved promises and emotional debt close to the ending;
- a deliberately declining final third with shorter chapters, lower forward-motion/emotional scores, higher repetition risk, and repeated structural patterns; and
- enough chapters/prose to force bounded developmental and manuscript-QA representations.

The benchmark currently verifies nine cross-cutting properties:

1. oversized continuity state compacts under its configured budget;
2. the buried chapter-4 callback is retrieved as an older relevant chapter rather than lost outside the rolling window;
3. a dormant unresolved character arc is detected;
4. a future planned touch for that character remains visible without resolving it early;
5. whole-book pacing recognizes that the manuscript is behind and raises the adaptive chapter target;
6. the penultimate chapter enters resolution-priority mode and forbids major new story debt;
7. the developmental manuscript map keeps every completed chapter while staying bounded;
8. the manuscript-QA map also keeps every completed chapter while staying bounded; and
9. gradual final-third quality/length degradation is surfaced by the whole-book trend audit even though no single chapter is designed as a catastrophic failure.

This is intentionally an architectural benchmark, not a claim that deterministic checks can judge prose quality. Its purpose is to catch regressions where a future code change quietly drops an old callback, stops detecting abandoned arcs, lets late-book story debt multiply again, loses whole-book coverage under context pressure, or becomes blind to gradual quality drift across otherwise individually acceptable chapters.

## Whole-book quality trends

The manuscript-QA stage also receives a deterministic quality-trend audit built from persisted chapter metadata. It compares the first and last thirds of the book, records raw and direction-normalized score changes, tracks chapter-length drift, finds long runs of the same chapter mode or ending-hook type, identifies recurring QA warnings, and carries forward chapters still marked revision-required.

Score direction matters. Craft/continuity signals such as forward motion, emotional depth, ending concreteness, irreversibility, and choice clarity are higher-is-better. Risk signals such as repetition risk, technical-escalation fatigue, and cuttable-chapter risk are lower-is-better. The audit records both the raw numeric delta and a `quality_direction_delta` whose sign always means the same thing: negative is degradation, positive is improvement.

The audit is advisory. It tells the manuscript editor where sustained drift deserves inspection; it does not assume that intentional late-book compression, a quiet act, or a deliberate tonal change is automatically wrong.

## Actual provider telemetry

Prompt-size telemetry uses a deliberately rough character-based token estimate before a request. When the provider exposes real usage metrics, Novel Generator now captures those as well and stores them under the successful stage attempt's `provider_metrics` metadata.

For Ollama, the stored metrics can include:

- `prompt_eval_count`;
- `eval_count`;
- total/load/prompt-eval/eval durations in milliseconds;
- prompt and completion tokens per second;
- `done_reason`; and
- actual prompt-context utilization when `num_ctx` is known.

For OpenAI-compatible local servers, the stored metrics can include:

- `prompt_tokens`;
- `completion_tokens`;
- `total_tokens`;
- cached prompt tokens when reported;
- reasoning tokens when reported;
- `finish_reason`; and
- the response model identifier.

Provider telemetry never stores the prompt or generated manuscript text. It is meant to answer practical tuning questions such as:

- Is chapter drafting becoming slower as context grows?
- Are structured stages consuming far more context than expected?
- Is a model stopping because of a length/stop condition rather than because the chapter is narratively complete?
- Is the configured Ollama context window mostly unused or close to saturation?
- Does a different local model provide materially better completion throughput on the same hardware?

The pre-request estimate and post-request actual count are both useful. Their difference also gives a rough signal for how inaccurate the simple character-based estimate is for the selected model/tokenizer.

## Interpreting failures

A benchmark failure should be treated as a long-form architecture regression even if ordinary unit tests still pass. The JSON report includes the selected recall chapters, dormant-character information, adaptive target, ending phase, context modes, context sizes, quality-score slope, late-book length drift, and quality risk flags to make the failure diagnosable without reproducing manuscript text.

The benchmark remains local-first and deterministic: it requires no embeddings, hosted service, API key, or additional inference call.