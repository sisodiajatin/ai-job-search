"""Keep a job's drafting and browser flow exclusive across processes."""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import threading

from .launcher import LaunchError

_local = threading.local()


@contextmanager
def application_lock(url: str, directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(url.encode()).hexdigest() + ".lock")
    key = str(path.resolve())
    held = getattr(_local, "held", set())
    if key in held:
        yield
        return
    with path.open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b" ")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as err:
            raise LaunchError("An application run for this job is already active. Use its existing window.", 409) from err
        _local.held = held
        held.add(key)
        try:
            yield
        finally:
            held.remove(key)
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
