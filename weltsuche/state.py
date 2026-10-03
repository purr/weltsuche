"""on-disk state: per-bucket breakers and spacing, and the result cache.

everything here is shared between processes on purpose. claude code starts one
weltsuche per session (five ran at once on 2026-09-29), and two sessions
hammering the same engine would look like exactly the bot traffic the spacing
exists to avoid.

every read-modify-write of state.json and of a cookie file happens under a
lock file held across processes, and every write is atomic (temp file, then
os.replace). on windows os.replace fails with PermissionError while another
process has the target open for a moment; writes and reads retry that for a
short while. a state file that cannot be read is never written back empty,
which would erase every open breaker.

the public methods of Breakers and Cache are coroutines that do their file
work in a worker thread: a lock another process holds (msvcrt retries up to
ten seconds) or a slow disk then waits there, not on the event loop that
serves every tool call of the session.
"""

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from .models import BreakerStatus, BucketState, StateFile

if os.name == "nt":
    import msvcrt
else:
    import fcntl

log = logging.getLogger("weltsuche.state")

STATE_VERSION = 1
CACHE_VERSION = 1

# page fetches space requests per host, which adds a bucket per site ever
# read; a bucket idle this long with a closed breaker is dropped on write
BUCKET_IDLE_S = 86400

# how long a transient windows sharing violation is retried, in total
SHARING_RETRY_S = 2.0


class StateUnavailable(Exception):
    """a state file exists but cannot be read right now."""


def _retry_sharing(action, path):
    """run `action`, retrying PermissionError (another process has the file
    open on windows) for up to SHARING_RETRY_S. the waits are milliseconds
    long, short enough to take on the event loop."""
    deadline = time.monotonic() + SHARING_RETRY_S
    delay = 0.005
    while True:
        try:
            return action()
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.1)
            log.debug("state: %s busy, retrying", Path(path).name)


@contextmanager
def file_lock(path):
    """an exclusive lock between weltsuche processes, held on `path`.lock."""
    lock_path = Path(f"{path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as fh:
        if os.name == "nt":
            fh.seek(0)
            # LK_LOCK retries for ten seconds, then raises OSError; holders keep
            # it for a read-modify-write of a small json file
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def write_json_atomic(path, obj):
    """temp file in the same directory, then os.replace. a half-written state
    file after a kill is worse than none."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
        _retry_sharing(lambda: os.replace(tmp, path), path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass  # the temp file is already gone or never made it; nothing to clean
        raise


def read_json(path, version):
    """a versioned json file. None when it is missing, broken or of another
    version (logged); StateUnavailable when it exists but cannot be read."""
    path = Path(path)
    if not path.exists():
        return None

    def load():
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    try:
        obj = _retry_sharing(load, path)
    except ValueError as e:
        log.warning("state: %s is not valid json (%s), discarding", path.name, e)
        return None
    except OSError as e:
        raise StateUnavailable(f"{path.name}: {type(e).__name__}: {e}") from e
    if not isinstance(obj, dict) or obj.get("version") != version:
        log.warning("state: %s has version %r, want %r, discarding", path.name, obj.get("version") if isinstance(obj, dict) else None, version)
        return None
    return obj


class Breakers:
    """per-bucket circuit breaker plus request spacing, persisted and shared
    by every weltsuche process."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self):
        obj = read_json(self.path, STATE_VERSION)
        if obj is None:
            return StateFile(version=STATE_VERSION)
        try:
            return StateFile.model_validate(obj)
        except ValidationError as e:
            log.warning("state: %s does not fit the state model (%s), starting fresh", self.path.name, e.errors()[:1])
            return StateFile(version=STATE_VERSION)

    @contextmanager
    def _state(self, write):
        """the state under both locks; written back when `write` is set."""
        with self._lock, file_lock(self.path):
            state = self._read()
            yield state
            if write:
                write_json_atomic(self.path, state.model_dump())

    def _bucket(self, name):
        """the state of one bucket, or a fresh one when the file cannot be
        read (spacing and breakers are protections; a read failure must not
        stop a search, and is logged)."""
        try:
            with self._state(write=False) as state:
                return state.engines.get(name, BucketState())
        except (StateUnavailable, OSError) as e:
            log.warning("state: cannot read %s for %s (%s); going ahead without it", self.path.name, name, e)
            return BucketState()

    def _update(self, name, change, what):
        try:
            with self._state(write=True) as state:
                change(state, state.engines.setdefault(name, BucketState()))
        except (StateUnavailable, OSError) as e:
            log.warning("state: could not record %s for %s (%s)", what, name, e)

    async def status(self, name):
        return await asyncio.to_thread(self._status, name)

    async def open(self, name, seconds, reason):
        await asyncio.to_thread(self._open, name, seconds, reason)

    async def reserve(self, name, gap):
        return await asyncio.to_thread(self._reserve, name, gap)

    async def all(self):
        return await asyncio.to_thread(self._all)

    def _status(self, name):
        """(is_open, reason, seconds_left)."""
        b = self._bucket(name)
        left = b.open_until - time.time()
        return (True, b.reason, int(left)) if left > 0 else (False, "", 0)

    def _open(self, name, seconds, reason):
        def change(state, b):
            b.open_until, b.reason = time.time() + seconds, reason
        self._update(name, change, "a breaker")
        log.warning("breaker: %s off for %ds (%s)", name, seconds, reason)

    def _reserve(self, name, gap):
        """claim the next request slot of `name`, at least `gap` seconds after
        the last one claimed by any process, and return how long to wait for
        it. claiming and waiting in one locked step is what keeps processes
        apart: reading the last time and writing the new one separately let
        several of them compute the same moment and fire together."""
        slot = [time.time()]

        def change(state, b):
            now = time.time()
            slot[0] = max(now, b.last_request + gap)
            b.last_request = slot[0]
            state.engines = {
                k: e for k, e in state.engines.items()
                if k == name or e.open_until > now or now - e.last_request < BUCKET_IDLE_S
            }
        self._update(name, change, "a request slot")
        return max(0.0, slot[0] - time.time())

    def _all(self):
        """{bucket: BreakerStatus} for every bucket in the file."""
        try:
            with self._state(write=False) as state:
                engines = dict(state.engines)
        except (StateUnavailable, OSError) as e:
            log.warning("state: cannot read %s (%s)", self.path.name, e)
            return {}
        now = time.time()
        return {
            name: BreakerStatus(
                open=b.open_until > now,
                reason=b.reason if b.open_until > now else "",
                seconds_left=int(b.open_until - now) if b.open_until > now else 0,
                last_request_ago_s=int(now - b.last_request) if b.last_request else None,
            )
            for name, b in engines.items()
        }


class Cache:
    """json blobs keyed by a string, one file each, ttl checked on read. a
    cache that cannot be read or written costs a repeat request, never an
    answer, so its failures are logged and passed over."""

    def __init__(self, directory):
        self.dir = Path(directory)

    async def get(self, key, ttl):
        return await asyncio.to_thread(self._get, key, ttl)

    async def put(self, key, value):
        await asyncio.to_thread(self._put, key, value)

    async def stats(self):
        return await asyncio.to_thread(self._stats)

    async def prune(self, ttl):
        return await asyncio.to_thread(self._prune, ttl)

    def _path(self, key):
        return self.dir / (hashlib.sha256(key.encode("utf-8")).hexdigest()[:32] + ".json")

    def _get(self, key, ttl):
        try:
            obj = read_json(self._path(key), CACHE_VERSION)
        except StateUnavailable as e:
            log.warning("cache: %s unreadable, treating as a miss (%s)", key[:80], e)
            return None
        if obj is None or time.time() - obj.get("ts", 0) > ttl:
            return None
        return obj.get("value")

    def _put(self, key, value):
        try:
            write_json_atomic(self._path(key), {"version": CACHE_VERSION, "ts": time.time(), "key": key, "value": value})
        except OSError as e:
            log.warning("cache: could not store %s (%s)", key[:80], e)

    def _files(self):
        # temp files of writes in flight are not entries
        return [f for f in self.dir.glob("*.json") if not f.name.startswith(".tmp-")] if self.dir.exists() else []

    def _stats(self):
        files, size = 0, 0
        for f in self._files():
            try:
                size += f.stat().st_size
                files += 1
            except FileNotFoundError:
                pass  # pruned by another process between the listing and the stat
        return {"files": files, "bytes": size}

    def _prune(self, ttl):
        """delete entries older than ttl. called at startup, cheap."""
        cutoff = time.time() - ttl
        n = 0
        for f in self._files():
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    n += 1
            except OSError:
                pass  # another process removed it first; that is the outcome we wanted
        return n
