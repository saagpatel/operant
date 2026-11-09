"""Atomic, private on-disk sitting state. Callers serialize operations with lock."""

from __future__ import annotations

import json
import hashlib
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

from sittings.canonical import canonical_bytes

SITTING_ID = re.compile(r"sit_[a-z2-7]{26}")


def atomic_json(path: Path, value: Any) -> None:
    """Replace a JSON file only after its complete contents have reached disk."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(canonical_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class SittingStore:
    """A single server owns a data directory; its threads share this lock.

    Expiration leaves state and finished records intact. It only revokes access
    and releases capacity. No answers or authentication tokens are logged.
    """

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / "sittings"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self._locks = tuple(threading.RLock() for _ in range(256))

    def lock_for(self, sitting_id: str):
        if not SITTING_ID.fullmatch(sitting_id):
            raise ValueError("invalid sitting id")
        stripe = int.from_bytes(hashlib.sha256(sitting_id.encode("ascii")).digest()[:4], "big")
        return self._locks[stripe % len(self._locks)]

    def directory(self, sitting_id: str) -> Path:
        if not SITTING_ID.fullmatch(sitting_id):
            raise ValueError("invalid sitting id")
        return self.root / sitting_id

    def load(self, sitting_id: str) -> dict:
        return json.loads((self.directory(sitting_id) / "state.json").read_text(encoding="utf-8"))

    def save(self, state: dict) -> None:
        atomic_json(self.directory(state["sitting_id"]) / "state.json", state)

    def create(self, state: dict) -> None:
        self.directory(state["sitting_id"]).mkdir(mode=0o700)
        self.save(state)

    def open_count(self, now: float) -> int:
        count = 0
        for directory in self.root.iterdir():
            if directory.is_dir() and SITTING_ID.fullmatch(directory.name):
                state = self.load(directory.name)
                count += state["expires_at_ms"] > int(now * 1000) and "result" not in state
        return count

    def stored_bytes(self) -> int:
        """Total current bytes owned by sitting state, records and receipts."""
        total = 0
        for path in self.data_dir.rglob("*"):
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        return total

    def write_record(self, sitting_id: str, record: dict) -> None:
        atomic_json(self.directory(sitting_id) / "record.json", record)

    def read_record(self, sitting_id: str) -> dict | None:
        path = self.directory(sitting_id) / "record.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def write_receipt(self, sitting_id: str, receipt: dict) -> None:
        atomic_json(self.directory(sitting_id) / f"{sitting_id}.receipt.json", receipt)

    def read_receipt(self, sitting_id: str) -> dict | None:
        path = self.directory(sitting_id) / f"{sitting_id}.receipt.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
