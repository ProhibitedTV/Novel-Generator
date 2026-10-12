# Continuity state lifecycle

The continuity ledger contains two different kinds of state and they must not be merged the same way over a long novel.

## Historical state

Historical fields are cumulative. Once an event happened, it remains part of the record even after it stops being active. Examples include:

- `timeline`
- `resolved_threads`
- `system_state_transitions`
- accumulated side-character decisions

These fields preserve provenance and let later QA or developmental passes reconstruct how the book reached its current state.

New continuity generation returns only the current chapter's historical changes.
The model supplies one concise `timeline_entry` and `timeline: []`; the application
appends the entry to preserved history. Newly resolved threads, side-character
decisions, and system transitions are merged with their stored predecessors.
Legacy checkpoints containing complete timelines remain readable, and merging
them cannot erase earlier historical entries. Live fields still require complete
current snapshots, including explicit empty lists/maps when state is resolved.

Continuity generation receives the actual chapter prose as its source of truth,
alongside the summary and prior ledger. It does not need to regenerate the entire
book's history in every response. This keeps late-chapter output from growing
with the cumulative timeline.

## Live state

Live fields describe what is still true *after the current chapter*. The continuity-update prompt asks the model to return the promises, threads, damage, fractures, and fallout that are still active. These fields therefore use post-chapter snapshot semantics rather than append-only merge semantics:

- `open_threads`
- `open_promises_by_name`
- `memory_damage`
- `trust_fractures`
- `civilian_pressure_points`
- `emotional_open_loops`

A live item may disappear when the chapter genuinely resolves or heals it. Resolved threads are removed from `open_threads` while remaining in `resolved_threads`; healed trust fractures and emotional loops can become empty; promises omitted from the new still-live snapshot are no longer treated as active story debt.

This distinction is important for long-form generation. Without it, old problems become "ghost debt": they remain in every later prompt even after the prose resolved them, which can cause models to reopen closed mysteries, repeat relationship conflicts, or manufacture unnecessary late-book closure work.

The lifecycle cleanup runs after the normal continuity merge, preserving cumulative history while making current story state genuinely current. Slightly rephrased thread resolutions are matched conservatively: exact normalized matches are accepted, and containment matching is used only for substantive phrases so short generic wording cannot accidentally close unrelated threads.

## Missing fields and saved checkpoints

An omitted live-state field is unknown, not a resolution. The ledger preserves existing promises,
threads, memory damage, trust fractures, civilian pressure, and emotional loops when a model omits
their fields. Explicit empty lists/maps still clear those fields. Provider JSON schemas request all
six live-state fields, and new chapter and editorial checkpoints preserve omitted-field information
with `exclude_unset=True` so a restart cannot turn missing information into a false resolution.
Older checkpoints that already serialized defaults cannot recover the original missing-field distinction.

Final reconciliation commits a refreshed summary and continuity update together
after both validate. A failed update preserves the earlier complete checkpoint
and the drafted chapter's completion status. The run still fails if required
metadata cannot be validated; draft completion does not certify final book
acceptance. The updated reconciliation contract invalidates older metadata
caches so their summaries and state are rebuilt from current prose.
