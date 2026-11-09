"""File-based approval queue and sealed-only publication."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

STATES = ("pending", "approved", "rejected")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _safe_id(sitting_id: str) -> str:
    if not isinstance(sitting_id, str) or not sitting_id or sitting_id in {".", ".."}:
        raise ValueError("invalid sitting id")
    if Path(sitting_id).name != sitting_id or "/" in sitting_id or "\\" in sitting_id:
        raise ValueError("invalid sitting id")
    return sitting_id


def _files(record: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for section in ("corpus", "scorer"):
        manifest = record.get(section)
        if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
            raise ValueError(f"record has no valid {section} manifest")
        output.extend(manifest["files"])
    return output


class ReviewQueue:
    """Queue sitting records and publish them only after successful verification."""

    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir) / "review"

    def _directory(self, state: str, sitting_id: str) -> Path:
        if state not in STATES:
            raise ValueError(f"unknown state: {state}")
        return self.root / state / _safe_id(sitting_id)

    def enqueue(self, record: dict[str, Any], receipt: dict[str, Any] | None) -> str:
        """Add a sitting; compatible with runner review_hook(record, receipt)."""
        sitting_id = _safe_id(record.get("sitting_id"))
        state = "pending" if receipt is not None else "rejected"
        reason = None if receipt is not None else "unsealed"
        directory = self._directory(state, sitting_id)
        if any((self.root / s / sitting_id).exists() for s in STATES):
            raise FileExistsError(f"sitting already queued: {sitting_id}")
        directory.mkdir(parents=True, exist_ok=False)
        submitted = _now()
        try:
            _atomic_json(directory / "record.json", record)
            if receipt is not None:
                _atomic_json(directory / "receipt.json", receipt)
            _atomic_json(directory / "status.json", {
                "state": state, "submitted_at": submitted,
                "decided_at": submitted if reason else None, "reason": reason,
            })
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return state

    def list(self, state: str) -> list[dict[str, Any]]:
        if state not in STATES:
            raise ValueError(f"unknown state: {state}")
        base = self.root / state
        if not base.exists():
            return []
        return [self.show(p.name) for p in sorted(base.iterdir()) if p.is_dir()]

    def show(self, sitting_id: str) -> dict[str, Any]:
        sitting_id = _safe_id(sitting_id)
        for state in STATES:
            directory = self.root / state / sitting_id
            if directory.is_dir():
                result = {"record": json.loads((directory / "record.json").read_text())}
                receipt = directory / "receipt.json"
                if receipt.exists():
                    result["receipt"] = json.loads(receipt.read_text())
                result["status"] = json.loads((directory / "status.json").read_text())
                return result
        raise FileNotFoundError(sitting_id)

    def approve(
        self, sitting_id: str, *, web_data: str | Path,
        verifier: Callable[..., Any] | None = None, pubkeys: Any = None,
        blob_source: str | Path,
    ) -> dict[str, Any]:
        sitting_id = _safe_id(sitting_id)
        current = self.show(sitting_id)
        if current["status"]["state"] == "rejected":
            raise ValueError("rejected sitting cannot be approved")
        if verifier is None:
            from sittings.receipt.verify import verify_receipt

            verifier = verify_receipt
        report = verifier(
            current.get("receipt"), record=current["record"], pubkeys=pubkeys,
            blob_root=Path(web_data) / "blobs", repo_root=blob_source,
        )
        if not report.ok:
            raise ValueError("receipt verification failed")

        web_data = Path(web_data)
        source = Path(blob_source)
        for item in _files(current["record"]):
            digest, rel = item["sha256"], Path(item["path"])
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("invalid bound file hash")
            blob = web_data / "blobs" / digest
            if blob.exists():
                if hashlib.sha256(blob.read_bytes()).hexdigest() != digest:
                    raise ValueError(f"existing blob has wrong hash: {digest}")
                continue
            live = (source / rel).resolve()
            if not live.is_relative_to(source.resolve()):
                raise ValueError("invalid bound file path")
            data = live.read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError(f"live bound file changed and blob is missing: {rel}")
            blob.parent.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(dir=blob.parent, prefix=f".{digest}.")
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, blob)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)

        (web_data / "receipts").mkdir(parents=True, exist_ok=True)
        (web_data / "records").mkdir(parents=True, exist_ok=True)
        rows_path = web_data / "rows.json"
        rows_doc = json.loads(rows_path.read_text()) if rows_path.exists() else {
            "format": "operant.instruments-rows/v1", "rows": [],
        }
        if rows_doc.get("format") != "operant.instruments-rows/v1" or not isinstance(rows_doc.get("rows"), list):
            raise ValueError("invalid rows.json")
        record = current["record"]
        receipt = current["receipt"]
        predicate = _receipt_predicate(receipt)
        binding = predicate.get("model_binding", record.get("model_binding", {}))
        row = {
            "sitting_id": sitting_id, "agent_label": record["agent_label"],
            "claimed_model": record["claimed_model"], **record["score"],
            "binding_level": binding.get("level"), "sealed_at": predicate["sealed_at"],
            "receipt": f"receipts/{sitting_id}.receipt.json",
            "record": f"records/{sitting_id}.record.json",
        }
        rows = [r for r in rows_doc["rows"] if r.get("sitting_id") != sitting_id]
        rows.append(row)
        rows.sort(key=lambda r: (-r["ocs"], r["sealed_at"]))

        # Stage all replaceable JSON in their target directories; rows.json is the commit marker.
        _atomic_json(web_data / "receipts" / f"{sitting_id}.receipt.json", receipt)
        _atomic_json(web_data / "records" / f"{sitting_id}.record.json", record)
        _atomic_json(rows_path, rows_doc | {"rows": rows})
        self._move(sitting_id, "approved", reason=None)
        return row

    def reject(self, sitting_id: str, reason: str) -> None:
        if not reason:
            raise ValueError("rejection reason is required")
        self._move(_safe_id(sitting_id), "rejected", reason=reason)

    def unpublish(self, sitting_id: str, *, web_data: str | Path) -> None:
        sitting_id = _safe_id(sitting_id)
        web_data = Path(web_data)
        rows_path = web_data / "rows.json"
        doc = json.loads(rows_path.read_text()) if rows_path.exists() else {
            "format": "operant.instruments-rows/v1", "rows": [],
        }
        doc["rows"] = [r for r in doc["rows"] if r.get("sitting_id") != sitting_id]
        _atomic_json(rows_path, doc)

    def _move(self, sitting_id: str, target: str, *, reason: str | None) -> None:
        found = None
        for state in STATES:
            path = self._directory(state, sitting_id)
            if path.is_dir():
                found = (state, path)
                break
        if found is None:
            raise FileNotFoundError(sitting_id)
        previous, directory = found
        status = json.loads((directory / "status.json").read_text())
        status.update(state=target, decided_at=_now(), reason=reason)
        _atomic_json(directory / "status.json", status)
        if previous == target:
            return
        destination = self._directory(target, sitting_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(directory, destination)


def _receipt_predicate(receipt: dict[str, Any]) -> dict[str, Any]:
    import base64

    payload = receipt["envelope"]["payload"]
    return json.loads(base64.b64decode(payload))["predicate"]
