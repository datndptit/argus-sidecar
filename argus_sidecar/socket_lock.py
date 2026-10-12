"""Exclusive Linux UDS ownership, including stale-socket recovery."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat


@contextmanager
def socket_lease(socket_path: str):
    path = Path(socket_path)
    if not path.is_absolute():
        raise ValueError("ARGUS_SOCKET must be an absolute path")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the lock inode: unlinking a held lock would let a second process
    # lock another inode and remove a socket that is still serving traffic.
    with open(str(path) + ".lock", "a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another sidecar owns this socket") from exc
        if path.is_symlink() or (path.exists() and not stat.S_ISSOCK(path.lstat().st_mode)):
            raise RuntimeError("refusing to replace a non-socket path")
        path.unlink(missing_ok=True)
        try:
            yield
        finally:
            if path.exists() and stat.S_ISSOCK(path.lstat().st_mode):
                path.unlink()
