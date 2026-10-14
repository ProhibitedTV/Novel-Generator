"""Process-held ownership for an explicitly exclusive local SQLite worker."""
from contextlib import contextmanager
from pathlib import Path
import os

from sqlalchemy.engine import make_url


@contextmanager
def exclusive_worker_lease(settings):
    if not settings.worker_exclusive:
        yield False
        return
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:" or url.database.startswith("file:"):
        raise RuntimeError("Exclusive worker ownership requires a local SQLite database file.")
    database = Path(url.database).resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    # Never unlink a lock file: replacing its inode can let two workers own it.
    handle = database.with_name(database.name + ".worker.lock").open("a+b")
    acquired = False
    try:
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            raise RuntimeError("Another exclusive worker owns this database; refusing to recover its runs.") from exc
        yield True
    finally:
        if acquired:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
