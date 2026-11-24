# Local worker ownership and restart recovery

`docker-compose.local.yml` enables `WORKER_EXCLUSIVE=true` for the workstation's
one SQLite worker. The worker acquires a nonblocking OS file lock beside the
database before migrations, startup recovery, or processing. It holds ownership
for its whole process lifetime. Another exclusive worker using that same database
file must exit without requeueing or claiming work.

The lock is released by the OS even if the owner is killed. A replacement worker
can then immediately recover fresh interrupted runs, preserving the persisted
chapter prose, plans, summaries, and editorial checkpoints. It need not wait for
the normal stale-heartbeat timeout. A saved cancellation request instead becomes
canceled, avoiding an unclaimable queued run.

When ownership is recovered, unfinished stage attempts for that run become
`interrupted`, with `WorkerInterrupted` and the recovery reason retained. Saved
successful/failed attempts and manuscript checkpoints stay unchanged. The
completion timestamp records when recovery detected the interruption; duration
is unknown and is not counted as inference time. Stale-only recovery leaves
fresh calls owned by another worker alone. Cancellation also closes abandoned
attempts rather than leaving them apparently active forever.

Outside this local override, ownership defaults to disabled. Startup remains
stale-only so fresh work assigned to other workers is left alone. Configuration
alone never authorizes immediate recovery: the entry point passes exclusive
ownership only after acquiring the lock. Exclusive ownership supports ordinary
SQLite files; in-memory, SQLite file-URI, and remote databases are rejected.

All workers sharing an exclusive local database must use this mode and resolve the
same database path. Do not combine an exclusive worker with legacy/nonexclusive
workers against that database. Do not mix Windows-native and Docker workers;
locking is qualified within each environment, not across the host/container
filesystem boundary. For independently managed multiple workers or
multi-host deployments, leave the flag false and use the ordinary stale-recovery
behavior. This is not a distributed worker lease.

Never delete a live `.worker.lock` file: replacing its inode would break the
ownership contract. An idle retained lock file is normal; ownership is determined
by the OS lock, not file existence.

Regression tests cover fresh checkpoint preservation, default fresh-owner
protection, competing subprocesses, abrupt owner death, unsupported database
types, and interrupted cancellation. Local acceptance requires a container
restart followed by `exclusive_worker_startup` requeueing and a new worker claim;
a RUNNING API status by itself is insufficient evidence.

## October 14 live qualification

PR #88 passed 570 CI tests and was merged before rebuilding both local services.
The revised Orchard run was active with three completed chapters and a fresh
Chapter 4 draft checkpoint. The replacement worker recorded
`exclusive_worker_startup` recovery at 14:01:28 UTC, claimed the run, reused its
saved Chapter 4 plan, and continued drafting/expanding instead of waiting an hour.

A separate process inside the running worker container attempted the same lease
and was blocked. The run's worker ID matched the new container hostname. All three
completed chapter hashes matched the pre-deployment snapshot, and the original
edition's manuscript and Markdown/DOCX checksums remained unchanged. The web
container was healthy and the worker could reach host Ollama.

The database backup, snapshot, and qualification record are retained locally under
`artifacts/runtime/pre-exclusive-deployment*` and
`artifacts/runtime/exclusive-recovery-qualification.json`. Generated worker lock
files are ignored by Git and retained on disk; they are not cleanup targets.


## Unattended operation and diagnostics

The run workspace shows a connection check, last persisted heartbeat, and the
provider cooldown separately. A heartbeat is evidence of a saved worker
update, not proof that a long inference is making progress. If the connection
drops, the page keeps its previous state and retries; reconnecting event streams
resume after the last seen sequence instead of duplicating the event list.

On failure or provider cooldown, **Copy diagnostics**, **View text**, and
**Download diagnostics** expose the current error, recent failure/recovery
events, actual failed stage for newly recorded failures, and recent failed
inference attempts. Inspect before sharing; provider error text can contain
private excerpts. The endpoint intentionally does not include manuscript text,
raw model outputs, or provider configuration records. Existing historical
failures may lack the original failed stage or causal chain.

Normal quality failures still stop with resumable checkpoints. If failure
handling itself escapes the pipeline, the worker exits nonzero; Compose
restarts it, and exclusive startup recovery restores interrupted work from
committed checkpoints. Both standard services now have a restart policy.
Persistent model/quality failures and exhausted recovery budgets require
intervention; this does not guarantee infinite retry.


### Retry exhaustion and interrupted repairs

Provider outages consume a bounded consecutive retry window. A manual resume
after a terminal failure starts a new transport retry window; it does not erase
historical failures or increase editorial repair budgets. Exhaustion and invalid
persisted cooldowns are recorded in the copyable diagnostics. Invalid cooldowns
fail only the affected run rather than leaving it queued indefinitely or crashing
the worker.

An interrupted autonomous revision is excluded from the repair budget when
worker recovery closes its matching unfinished inference attempt. Completed
repairs and ordinary quality rejections remain charged. Provider transport
interruption is recorded even when its automatic retry budget is exhausted.


### Liveness during slow inference

The worker now writes an owner-scoped heartbeat in a separate short database
transaction while processing a run. It does not touch prose, editorial evidence,
or the run's general modification timestamp. Pulses stop when processing exits
or the owner/status changes. A database write failure is logged and retried.
The dashboard displays elapsed inference time separately: a fresh heartbeat
does not prove that the provider is generating tokens or that a quality gate
will pass. Provider timeouts and bounded retry policies remain in effect.
This does not implement a distributed lease or qualify mixed workers.
