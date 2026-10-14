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
