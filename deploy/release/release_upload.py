"""Bounded, resumable staging spool used only by the restricted SSH gate."""
import os
import shutil
import time

import rastisi_common as C
from rastisi_release import Lock

CHUNK_SIZE = 2 * 1024 * 1024
LEASE_SECONDS = 24 * 3600


class Upload:
    """One artifact slot per deployment layout. Call methods under the spool lock."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.root = cfg.staging / "resumable"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o750)
        self.metadata = self.root / "transfer.json"
        self.partial = self.root / "artifact.partial"
        self.ready = self.root / "artifact.tar.gz"
        self.lock = Lock(self.root / "upload.lock")

    def session(self, sha, digest, size, busy):
        state = C.read_json(self.metadata, {})
        if not state and self.metadata.exists():
            raise ValueError("upload checkpoint is unreadable; inspect staging state")
        if state and (state["sha"], state["digest"], state["size"]) == (sha, digest, size):
            return state
        if state:
            if busy(state.get("run")):
                raise ValueError("another artifact has a staging job; inspect its run before proceeding")
            if not state.get("run") and time.time() - state["updated"] < LEASE_SECONDS:
                raise ValueError("another upload owns the staging slot until its lease expires")
        for path in (self.partial, self.ready):
            path.unlink(missing_ok=True)
        self.partial.touch(mode=0o640)
        state = {"sha": sha, "digest": digest, "size": size, "offset": 0, "updated": time.time()}
        self.save(state)
        return state

    def save(self, state):
        state["updated"] = time.time()
        C.atomic_write_json(self.metadata, state)

    def append(self, state, offset, data):
        committed = state["offset"]
        if offset < committed and offset + len(data) <= committed:
            # Lost reply after commit. The client got its chunk bytes from the
            # same whole-file digest and finally revalidates that digest.
            return committed
        if self.ready.exists() or offset != committed:
            raise ValueError("chunk offset disagrees with checkpoint; query upload-offset")
        reserve = self.cfg.int("min_free_mb", 500) * 1024 * 1024
        if shutil.disk_usage(self.root).free < reserve + len(data):
            raise ValueError("insufficient disk space for upload while preserving the configured reserve")
        with self.partial.open("r+b") as stream:
            if stream.seek(0, os.SEEK_END) < committed:
                raise ValueError("partial artifact is shorter than its durable checkpoint")
            stream.truncate(committed)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        state["offset"] = committed + len(data)
        self.save(state)
        return state["offset"]

    def finish(self, state):
        if state["offset"] != state["size"]:
            raise ValueError("artifact upload is incomplete")
        candidate = self.ready if self.ready.exists() else self.partial
        if candidate.stat().st_size != state["size"] or C.sha256_file(candidate) != state["digest"]:
            raise ValueError("whole-artifact checksum mismatch; inspect and discard the staging slot")
        if candidate == self.partial:
            os.replace(self.partial, self.ready)
            C.fsync_directory(self.root)
        return self.ready
