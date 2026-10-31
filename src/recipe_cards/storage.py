from contextlib import contextmanager
import fcntl
import json
import logging
import os
from pathlib import Path
import tempfile

LOG = logging.getLogger(__name__)


def atomic(path, write):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".recipe-tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            write(f)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(tmp).unlink(missing_ok=True)


def read(path):
    try:
        p = Path(path)
        if p.stat().st_size > 4_000_000:
            raise ValueError("State too large")
        return json.loads(p.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        LOG.warning("Cannot read %s: %s", path, exc)
        return None


def save(path, value):
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    atomic(path, lambda f: f.write(payload))


@contextmanager
def lock(cfg):
    cfg.state.mkdir(parents=True, exist_ok=True)
    with (cfg.state / "lock").open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another recipe-card command is active") from None
        yield


def remove_temps(folder, now):
    if not folder.exists():
        return
    for p in folder.glob(".recipe-tmp-*"):
        if not p.is_symlink() and p.is_file() and now - p.stat().st_mtime > 86400:
            p.unlink(missing_ok=True)
