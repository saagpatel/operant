"""File-based approval queue and sealed-only publication."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Callable

from sittings.canonical import canonical_sha256

STATES = ("pending", "approved", "rejected")


@contextmanager
def _publication_lock(web_data: Path):
    web_data.mkdir(parents=True, exist_ok=True)
    # Keep the lock file in place: replacing/unlinking it would split concurrent lockers.
    with (web_data / ".publication.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _merge_pubkeys(pubkeys: Any) -> dict[str, Any]:
    documents = pubkeys if isinstance(pubkeys, list) else [pubkeys]
    merged = {}
    for document in documents:
        if (
            not isinstance(document, dict)
            or document.get("format") != "operant.sittings-pubkey/v1"
            or not isinstance(document.get("keys"), list)
            or not document["keys"]
        ):
            raise ValueError("invalid trusted public key document")
        for entry in document["keys"]:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("keyid"), str)
                or not re.fullmatch(r"ed25519:[0-9a-f]{16}", entry["keyid"])
                or entry.get("alg") != "ed25519"
                or any(not isinstance(entry.get(field), str) or not entry[field]
                       for field in ("public_key_pem", "spki_der_b64"))
            ):
                raise ValueError("invalid trusted public key entry")
            previous = merged.get(entry["keyid"])
            if previous is not None and any(
                previous[field] != entry[field] for field in ("public_key_pem", "spki_der_b64")
            ):
                raise ValueError("conflicting trusted public key bytes for keyid")
            merged[entry["keyid"]] = entry
    if not merged:
        raise ValueError("trusted public key document is required for publication")
    return {"format": "operant.sittings-pubkey/v1", "keys": list(merged.values())}


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


def _validated_files(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate manifests before any source or blob path is read."""
    validated = []
    for section in ("corpus", "scorer"):
        manifest = record.get(section)
        if not isinstance(manifest, dict) or set(manifest) != {"sha256", "files"}:
            raise ValueError(f"record has no valid {section} manifest")
        aggregate = manifest.get("sha256")
        files = manifest.get("files")
        if not isinstance(aggregate, str) or len(aggregate) != 64 or any(
            char not in "0123456789abcdef" for char in aggregate
        ):
            raise ValueError(f"invalid {section} manifest hash")
        if not isinstance(files, list) or not files:
            raise ValueError(f"invalid {section} manifest files")
        paths = []
        for item in files:
            if not isinstance(item, dict) or set(item) != {"path", "sha256", "bytes"}:
                raise ValueError("invalid bound file entry")
            rel, digest, size = item["path"], item["sha256"], item["bytes"]
            if not isinstance(rel, str) or not rel or rel == "." or "\\" in rel:
                raise ValueError("invalid bound file path")
            posix = PurePosixPath(rel)
            if posix.is_absolute() or not posix.parts or ".." in posix.parts or "." in posix.parts or str(posix) != rel:
                raise ValueError("invalid bound file path")
            if (
                not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
            ):
                raise ValueError("invalid bound file hash")
            if type(size) is not int or size < 0:
                raise ValueError("invalid bound file size")
            paths.append(rel)
        if paths != sorted(set(paths)):
            raise ValueError("bound file paths must be sorted and unique")
        expected = canonical_sha256([{"path": item["path"], "sha256": item["sha256"]} for item in files])
        if expected != aggregate:
            raise ValueError(f"{section} manifest aggregate mismatch")
        validated.extend(files)
    return validated


def _score_rate(value: Any) -> Decimal:
    if not isinstance(value, str) or not re.fullmatch(r"-?\d+\.\d{6}", value):
        raise ValueError("score rates must be six-decimal strings")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid score rate") from exc
    if not parsed.is_finite() or len(value.partition(".")[2]) != 6:
        raise ValueError("score rates must have exactly six decimals")
    return parsed


def _safe_source_bytes(source: Path, rel: str) -> bytes:
    absolute_source = Path(os.path.abspath(source))
    cursor = Path(absolute_source.anchor)
    for part in absolute_source.parts[1:]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("symlink refused in bound source path")
    source = absolute_source.resolve()
    candidate = source / PurePosixPath(rel)
    cursor = source
    for part in PurePosixPath(rel).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("symlink refused in bound source path")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(source):
        raise ValueError("bound source path escapes blob source")
    return candidate.read_bytes()


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
        for existing_state in STATES:
            existing = self._directory(existing_state, sitting_id)
            if existing.exists():
                existing_record = json.loads((existing / "record.json").read_text(encoding="utf-8"))
                existing_receipt_path = existing / "receipt.json"
                existing_receipt = (
                    json.loads(existing_receipt_path.read_text(encoding="utf-8"))
                    if existing_receipt_path.exists() else None
                )
                if existing_record == record and existing_receipt == receipt:
                    return existing_state
                raise FileExistsError(f"sitting already queued with different bytes: {sitting_id}")
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
        score = current["record"].get("score")
        if not isinstance(score, dict) or score.get("publishable") is not True:
            reasons = score.get("not_publishable_reasons", []) if isinstance(score, dict) else []
            reason_text = ", ".join(str(reason) for reason in reasons) or "record is not publishable"
            raise ValueError(f"non-publishable sitting cannot be approved: {reason_text}")
        if verifier is None:
            from sittings.receipt.verify import verify_receipt

            verifier = verify_receipt
        pubkeys = _merge_pubkeys(pubkeys)
        report = verifier(
            current.get("receipt"), record=current["record"], pubkeys=pubkeys,
            blob_root=Path(web_data) / "blobs", repo_root=blob_source,
        )
        if not report.ok:
            raise ValueError("receipt verification failed")

        web_data = Path(web_data)
        with _publication_lock(web_data):
            return self._publish(sitting_id, current, web_data, pubkeys, Path(blob_source))

    def _publish(
        self, sitting_id: str, current: dict[str, Any], web_data: Path,
        pubkeys: dict[str, Any], source: Path,
    ) -> dict[str, Any]:
        """Publish verified bytes while holding the web directory's publication lock."""
        blob_directory = web_data / "blobs"
        if blob_directory.is_symlink():
            raise ValueError("symlink refused in content-addressed blob directory")
        blob_directory.mkdir(parents=True, exist_ok=True)
        if blob_directory.is_symlink():
            raise ValueError("symlink refused in content-addressed blob directory")
        for item in _validated_files(current["record"]):
            digest, rel = item["sha256"], item["path"]
            blob = blob_directory / digest
            if blob.is_symlink():
                raise ValueError("symlink refused in content-addressed blob")
            if blob.exists():
                if not blob.is_file() or hashlib.sha256(blob.read_bytes()).hexdigest() != digest:
                    raise ValueError(f"existing blob has wrong hash: {digest}")
                if blob.stat().st_size != item["bytes"]:
                    raise ValueError(f"existing blob has wrong size: {digest}")
                continue
            data = _safe_source_bytes(source, rel)
            if len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != digest:
                raise ValueError(f"live bound file changed and blob is missing: {rel}")
            fd, temp = tempfile.mkstemp(dir=blob_directory, prefix=f".{digest}.")
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
        binding = record.get("model_binding", {})
        for field in ("ocs", "tpr", "fpr", "accuracy", "safe_correct_rate"):
            _score_rate(record["score"].get(field))
        row = {
            "sitting_id": sitting_id, "agent_label": record["agent_label"],
            "claimed_model": record["claimed_model"], **record["score"],
            "binding_level": binding.get("level"), "sealed_at": predicate["sealed_at"],
            "observed_model": binding.get("observed_model"),
            "claim_status": binding.get("evidence", {}).get("claim_status", "unknown"),
            "not_publishable_reasons": record["score"].get("not_publishable_reasons", []),
            "receipt": f"receipts/{sitting_id}.receipt.json",
            "record": f"records/{sitting_id}.record.json",
        }
        rows = [r for r in rows_doc["rows"] if r.get("sitting_id") != sitting_id]
        rows.append(row)
        rows.sort(key=lambda r: (-_score_rate(r["ocs"]), r["sealed_at"]))

        existing_pubkeys = web_data / "sittings-pubkey.json"
        if existing_pubkeys.exists():
            previous_keys = json.loads(existing_pubkeys.read_text(encoding="utf-8"))
            pubkeys = _merge_pubkeys([previous_keys, pubkeys])
        # Stage all replaceable JSON in their target directories; rows.json is the commit marker.
        _atomic_json(web_data / "receipts" / f"{sitting_id}.receipt.json", receipt)
        _atomic_json(web_data / "records" / f"{sitting_id}.record.json", record)
        _atomic_json(existing_pubkeys, pubkeys)
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
        with _publication_lock(web_data):
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
