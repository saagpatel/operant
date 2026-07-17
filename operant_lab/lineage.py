"""Local structural lineage for private OPERANT run receipts."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ZERO_SHA256 = "0" * 64
BASELINE_SCHEMA = "operant-receipt-lineage-baseline.v1"
ENTRY_SCHEMA = "operant-receipt-lineage-entry.v1"
LINK_SCHEMA = "operant-receipt-lineage-link.v1"
BASELINE_CLAIM = "PRESENCE_AT_LOCAL_ACTIVATION_ONLY"
AUTHORSHIP = "UNKNOWN"
HISTORY_IMMUTABILITY = "NOT_PROVEN"
MAX_SEQUENCE = 9_007_199_254_740_991
LINEAGE_ROOT = Path("lab") / "receipt-lineage"
RUNS_ROOT = Path("lab") / "runs"
BASELINE_NAME = "baseline.json"
JOURNAL_NAME = "journal.jsonl"
LOCK_NAME = ".lock"

BASELINE_KEYS = {
    "schema",
    "captured_at",
    "claim_boundary",
    "receipt_count",
    "receipts",
    "receipts_sha256",
    "authorship",
    "history_immutability",
}
ENTRY_KEYS = {
    "schema",
    "entry_type",
    "sequence",
    "previous_entry_sha256",
    "baseline_sha256",
    "receipt_key_sha256",
    "receipt_sha256",
    "manifest_core_sha256",
    "authorship",
    "entry_sha256",
}
LINK_KEYS = {
    "schema",
    "sequence",
    "previous_entry_sha256",
    "baseline_sha256",
    "receipt_key_sha256",
    "manifest_core_sha256",
    "authorship",
}
BASELINE_RECEIPT_KEYS = {"receipt_key_sha256", "receipt_sha256"}


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _write_bytes_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=".lineage-",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _paths(root: Path) -> tuple[Path, Path, Path]:
    lineage_root = root / LINEAGE_ROOT
    return (
        lineage_root / BASELINE_NAME,
        lineage_root / JOURNAL_NAME,
        lineage_root / LOCK_NAME,
    )


@contextmanager
def _lineage_lock(root: Path) -> Iterator[None]:
    _, _, lock_path = _paths(root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def lineage_state_exists(root: Path) -> bool:
    baseline_path, journal_path, _ = _paths(root)
    return baseline_path.exists() or journal_path.exists()


def _receipt_key(path: Path, runs_root: Path) -> str:
    return path.relative_to(runs_root).as_posix()


def _receipt_key_hash(key: str) -> str:
    return _canonical_hash(
        {
            "domain": "operant-receipt-lineage-key.v1",
            "relative_receipt_path": key,
        }
    )


def _expected_receipt_key(data: dict[str, Any]) -> str | None:
    manifest = data.get("manifest")
    if not isinstance(manifest, dict):
        return None
    run_label = manifest.get("run_label")
    case_id = manifest.get("case_id")
    if (
        not isinstance(run_label, str)
        or not run_label
        or Path(run_label).name != run_label
        or not isinstance(case_id, str)
        or not case_id
    ):
        return None
    return f"{run_label}/{case_id.replace('/', '_')}.json"


def _canonical_new_receipt_key(data: dict[str, Any]) -> str:
    manifest = data.get("manifest")
    if not isinstance(manifest, dict):
        raise ValueError("receipt lacks a manifest")
    run_label = manifest.get("run_label")
    case_id = manifest.get("case_id")
    if (
        not isinstance(run_label, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_label)
        or not isinstance(case_id, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", case_id)
    ):
        raise ValueError(
            "new receipt identifiers must be canonical filename-safe values"
        )
    return f"{run_label}/{case_id}.json"


def _scan_receipts(
    root: Path,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    runs_root = root / RUNS_ROOT
    receipts: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    if not runs_root.exists():
        return receipts, errors
    for path in sorted(runs_root.glob("*/*.json")):
        try:
            resolved_runs_root = runs_root.resolve()
            resolved_path = path.resolve()
            resolved_path.relative_to(resolved_runs_root)
            if (
                path.is_symlink()
                or path.parent.is_symlink()
                or not path.is_file()
                or resolved_path != path.absolute()
            ):
                errors.append("receipt lineage encountered a non-regular receipt")
                continue
            raw = path.read_bytes()
            data = json.loads(raw)
            if not isinstance(data, dict):
                errors.append("receipt lineage encountered a non-object receipt")
                continue
            key = _receipt_key(path, runs_root)
            if _expected_receipt_key(data) != key:
                errors.append("receipt lineage encountered an identity mismatch")
                continue
            key_hash = _receipt_key_hash(key)
            if key_hash in receipts:
                errors.append("receipt lineage encountered a key collision")
                continue
            receipts[key_hash] = {
                "receipt_sha256": _sha256_bytes(raw),
                "data": data,
            }
        except (OSError, UnicodeError, json.JSONDecodeError):
            errors.append("receipt lineage could not read a receipt")
    return receipts, errors


def _baseline_payload(
    receipts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rows = sorted(
        (
            {
                "receipt_key_sha256": key_hash,
                "receipt_sha256": receipt["receipt_sha256"],
            }
            for key_hash, receipt in receipts.items()
        ),
        key=lambda row: row["receipt_key_sha256"],
    )
    return {
        "schema": BASELINE_SCHEMA,
        "captured_at": _utc_now(),
        "claim_boundary": BASELINE_CLAIM,
        "receipt_count": len(rows),
        "receipts": rows,
        "receipts_sha256": _canonical_hash(rows),
        "authorship": AUTHORSHIP,
        "history_immutability": HISTORY_IMMUTABILITY,
    }


def _entry_hash(entry: dict[str, Any]) -> str:
    return _canonical_hash(
        {
            key: value
            for key, value in entry.items()
            if key != "entry_sha256"
        }
    )


def _genesis_entry(baseline_sha256: str) -> dict[str, Any]:
    entry = {
        "schema": ENTRY_SCHEMA,
        "entry_type": "GENESIS",
        "sequence": 0,
        "previous_entry_sha256": ZERO_SHA256,
        "baseline_sha256": baseline_sha256,
        "receipt_key_sha256": "NONE",
        "receipt_sha256": "NONE",
        "manifest_core_sha256": "UNKNOWN",
        "authorship": AUTHORSHIP,
        "entry_sha256": "UNKNOWN",
    }
    entry["entry_sha256"] = _entry_hash(entry)
    return entry


def _initialize_locked(root: Path, *, allow_existing_receipts: bool) -> None:
    baseline_path, journal_path, _ = _paths(root)
    if baseline_path.exists() or journal_path.exists():
        raise FileExistsError("receipt lineage is already initialized")
    receipts, errors = _scan_receipts(root)
    if errors:
        raise RuntimeError("; ".join(sorted(set(errors))))
    if receipts and not allow_existing_receipts:
        raise RuntimeError(
            "existing receipts require explicit lineage initialization"
        )
    second_receipts, second_errors = _scan_receipts(root)
    if second_errors:
        raise RuntimeError("; ".join(sorted(set(second_errors))))
    if {
        key: value["receipt_sha256"] for key, value in receipts.items()
    } != {
        key: value["receipt_sha256"] for key, value in second_receipts.items()
    }:
        raise RuntimeError(
            "receipt inventory changed during lineage initialization"
        )
    baseline = _baseline_payload(receipts)
    baseline_bytes = _json_bytes(baseline)
    baseline_sha256 = _sha256_bytes(baseline_bytes)
    genesis_bytes = (
        json.dumps(
            _genesis_entry(baseline_sha256),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    _write_bytes_exclusive(baseline_path, baseline_bytes)
    _write_bytes_exclusive(journal_path, genesis_bytes)
    post_errors, _ = _load_and_validate_state(root)
    if post_errors:
        raise RuntimeError(
            "lineage initialization did not stabilize: "
            + "; ".join(sorted(set(post_errors)))
        )


def initialize_receipt_lineage(root: Path) -> None:
    """Snapshot current local receipt presence without claiming run chronology."""
    root = root.resolve()
    with _lineage_lock(root):
        _initialize_locked(root, allow_existing_receipts=True)


def _validate_baseline(
    baseline: Any,
    *,
    actual_receipts: dict[str, dict[str, Any]],
    errors: list[str],
) -> dict[str, str]:
    baseline_receipts: dict[str, str] = {}
    if not isinstance(baseline, dict) or set(baseline) != BASELINE_KEYS:
        errors.append("receipt lineage baseline fields are not exact")
        return baseline_receipts
    if (
        baseline.get("schema") != BASELINE_SCHEMA
        or baseline.get("claim_boundary") != BASELINE_CLAIM
        or baseline.get("authorship") != AUTHORSHIP
        or baseline.get("history_immutability") != HISTORY_IMMUTABILITY
    ):
        errors.append("receipt lineage baseline claim is invalid")
    captured_at = baseline.get("captured_at")
    if not isinstance(captured_at, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
        captured_at,
    ):
        errors.append("receipt lineage baseline timestamp is invalid")
    rows = baseline.get("receipts")
    if not isinstance(rows, list):
        errors.append("receipt lineage baseline receipts are invalid")
        return baseline_receipts
    if rows != sorted(
        rows,
        key=lambda row: (
            row.get("receipt_key_sha256", "")
            if isinstance(row, dict)
            else ""
        ),
    ):
        errors.append("receipt lineage baseline receipts are not sorted")
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != BASELINE_RECEIPT_KEYS
            or not _is_sha256(row.get("receipt_key_sha256"))
            or not _is_sha256(row.get("receipt_sha256"))
        ):
            errors.append("receipt lineage baseline receipt is invalid")
            continue
        key_hash = row["receipt_key_sha256"]
        if key_hash in baseline_receipts:
            errors.append("receipt lineage baseline contains a duplicate")
            continue
        baseline_receipts[key_hash] = row["receipt_sha256"]
        actual = actual_receipts.get(key_hash)
        if actual is None:
            errors.append("receipt lineage baseline receipt is missing")
        elif actual["receipt_sha256"] != row["receipt_sha256"]:
            errors.append("receipt lineage baseline receipt bytes changed")
    count = baseline.get("receipt_count")
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or count != len(rows)
    ):
        errors.append("receipt lineage baseline count is invalid")
    if baseline.get("receipts_sha256") != _canonical_hash(rows):
        errors.append("receipt lineage baseline digest mismatch")
    return baseline_receipts


def _validate_entry(
    entry: Any,
    *,
    expected_sequence: int,
    expected_previous: str,
    baseline_sha256: str,
    errors: list[str],
) -> bool:
    if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
        errors.append("receipt lineage journal entry fields are not exact")
        return False
    if (
        entry.get("schema") != ENTRY_SCHEMA
        or not isinstance(entry.get("sequence"), int)
        or isinstance(entry.get("sequence"), bool)
        or not 0 <= entry["sequence"] <= MAX_SEQUENCE
        or entry.get("sequence") != expected_sequence
        or entry.get("previous_entry_sha256") != expected_previous
        or entry.get("baseline_sha256") != baseline_sha256
        or entry.get("authorship") != AUTHORSHIP
        or entry.get("entry_sha256") != _entry_hash(entry)
    ):
        errors.append("receipt lineage journal chain is invalid")
        return False
    return True


def _validate_link(
    link: Any,
    entry: dict[str, Any],
    *,
    errors: list[str],
) -> None:
    if not isinstance(link, dict) or set(link) != LINK_KEYS:
        errors.append("receipt lineage link fields are not exact")
        return
    expected = {
        "schema": LINK_SCHEMA,
        "sequence": entry["sequence"],
        "previous_entry_sha256": entry["previous_entry_sha256"],
        "baseline_sha256": entry["baseline_sha256"],
        "receipt_key_sha256": entry["receipt_key_sha256"],
        "manifest_core_sha256": entry["manifest_core_sha256"],
        "authorship": AUTHORSHIP,
    }
    if link != expected:
        errors.append("receipt lineage link does not match journal entry")


def _load_and_validate_state(
    root: Path,
) -> tuple[list[str], dict[str, Any]]:
    baseline_path, journal_path, _ = _paths(root)
    errors: list[str] = []
    state: dict[str, Any] = {}
    if not baseline_path.exists() or not journal_path.exists():
        return ["receipt lineage state is incomplete"], state
    if (
        baseline_path.is_symlink()
        or journal_path.is_symlink()
        or not baseline_path.is_file()
        or not journal_path.is_file()
    ):
        return ["receipt lineage state is not regular files"], state
    actual_receipts, scan_errors = _scan_receipts(root)
    errors.extend(scan_errors)
    try:
        baseline_bytes = baseline_path.read_bytes()
        baseline = json.loads(baseline_bytes)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ["receipt lineage baseline is unreadable"], state
    baseline_sha256 = _sha256_bytes(baseline_bytes)
    baseline_receipts = _validate_baseline(
        baseline,
        actual_receipts=actual_receipts,
        errors=errors,
    )
    try:
        journal_bytes = journal_path.read_bytes()
        journal_text = journal_bytes.decode("utf-8")
    except (OSError, UnicodeError):
        return ["receipt lineage journal is unreadable"], state
    if (
        not journal_bytes
        or not journal_bytes.endswith(b"\n")
        or b"\r" in journal_bytes
    ):
        return ["receipt lineage journal framing is invalid"], state
    raw_lines = journal_text[:-1].split("\n")
    if not raw_lines or any(not line for line in raw_lines):
        return ["receipt lineage journal framing is invalid"], state
    entries = []
    for line in raw_lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            errors.append("receipt lineage journal entry is unreadable")
            continue
        canonical_line = json.dumps(
            entry,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if line != canonical_line:
            errors.append("receipt lineage journal entry is not canonical")
        entries.append(entry)
    previous = ZERO_SHA256
    journal_receipts: dict[str, str] = {}
    for sequence, entry in enumerate(entries):
        if not _validate_entry(
            entry,
            expected_sequence=sequence,
            expected_previous=previous,
            baseline_sha256=baseline_sha256,
            errors=errors,
        ):
            continue
        if sequence == 0:
            if (
                entry.get("entry_type") != "GENESIS"
                or entry.get("receipt_key_sha256") != "NONE"
                or entry.get("receipt_sha256") != "NONE"
                or entry.get("manifest_core_sha256") != "UNKNOWN"
            ):
                errors.append("receipt lineage genesis is invalid")
        else:
            key_hash = entry.get("receipt_key_sha256")
            receipt_sha256 = entry.get("receipt_sha256")
            manifest_core_sha256 = entry.get("manifest_core_sha256")
            if (
                entry.get("entry_type") != "RECEIPT"
                or not _is_sha256(key_hash)
                or not _is_sha256(receipt_sha256)
                or not _is_sha256(manifest_core_sha256)
                or key_hash in journal_receipts
                or key_hash in baseline_receipts
            ):
                errors.append("receipt lineage receipt entry is invalid")
            else:
                journal_receipts[key_hash] = receipt_sha256
                actual = actual_receipts.get(key_hash)
                if actual is None:
                    errors.append("receipt lineage journal receipt is missing")
                else:
                    if actual["receipt_sha256"] != receipt_sha256:
                        errors.append("receipt lineage journal receipt bytes changed")
                    manifest = actual["data"].get("manifest")
                    actual_core = (
                        manifest.get("manifest_core_sha256")
                        if isinstance(manifest, dict)
                        else None
                    )
                    if actual_core != manifest_core_sha256:
                        errors.append("receipt lineage manifest digest mismatch")
                    _validate_link(
                        actual["data"].get("receipt_lineage"),
                        entry,
                        errors=errors,
                    )
        if isinstance(entry, dict) and _is_sha256(entry.get("entry_sha256")):
            previous = entry["entry_sha256"]
    expected_keys = set(baseline_receipts) | set(journal_receipts)
    if set(actual_receipts) != expected_keys:
        errors.append("receipt lineage contains an orphan or untracked receipt")
    state.update(
        {
            "baseline_sha256": baseline_sha256,
            "entries": entries,
            "actual_receipts": actual_receipts,
            "baseline_receipts": baseline_receipts,
            "journal_receipts": journal_receipts,
        }
    )
    return errors, state


def validate_receipt_lineage(root: Path) -> list[str]:
    """Validate the complete local receipt set and journal without raising."""
    try:
        errors, _ = _load_and_validate_state(root.resolve())
        return errors
    except (
        AttributeError,
        IndexError,
        KeyError,
        OSError,
        OverflowError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        return ["receipt lineage contains invalid types"]


def receipt_lineage_block_reason(root: Path) -> str | None:
    """Return a global fail-closed reason when local lineage is configured."""
    root = root.resolve()
    baseline_path, journal_path, _ = _paths(root)
    if not baseline_path.exists() and not journal_path.exists():
        receipts, errors = _scan_receipts(root)
        return (
            "receipt_lineage_invalid"
            if receipts or errors
            else None
        )
    return "receipt_lineage_invalid" if validate_receipt_lineage(root) else None


def _append_journal_entry(path: Path, entry: dict[str, Any]) -> None:
    payload = (
        json.dumps(
            entry,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND)
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(fd, payload[offset:])
            if written < 1:
                raise OSError("receipt lineage journal append made no progress")
            offset += written
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_directory(path.parent)


def publish_receipt_with_lineage(
    root: Path,
    path: Path,
    report_data: dict[str, Any],
) -> Path:
    """Publish one receipt and its chained journal entry under one local lock."""
    root = root.resolve()
    requested_path = path
    path = path.resolve()
    runs_root = (root / RUNS_ROOT).resolve()
    try:
        key = path.relative_to(runs_root).as_posix()
    except ValueError as exc:
        raise ValueError("receipt path is outside the runs root") from exc
    if Path(key).parts != (path.parent.name, path.name):
        raise ValueError("receipt path must be exactly one label directory deep")
    if key != _canonical_new_receipt_key(report_data):
        raise ValueError("receipt path does not match canonical logical identity")
    manifest = report_data.get("manifest")
    manifest_core_sha256 = (
        manifest.get("manifest_core_sha256")
        if isinstance(manifest, dict)
        else None
    )
    if not _is_sha256(manifest_core_sha256):
        raise ValueError("receipt lacks a bound manifest core digest")
    with _lineage_lock(root):
        baseline_path, journal_path, _ = _paths(root)
        if not baseline_path.exists() and not journal_path.exists():
            receipts, scan_errors = _scan_receipts(root)
            if scan_errors:
                raise RuntimeError("; ".join(sorted(set(scan_errors))))
            if receipts:
                raise RuntimeError(
                    "existing receipts require explicit lineage initialization"
                )
            _initialize_locked(root, allow_existing_receipts=False)
        errors, state = _load_and_validate_state(root)
        if errors:
            raise RuntimeError(
                "refusing receipt publication with invalid lineage: "
                + "; ".join(sorted(set(errors)))
            )
        entries = state["entries"]
        previous_entry = entries[-1]
        sequence = len(entries)
        key_hash = _receipt_key_hash(key)
        link = {
            "schema": LINK_SCHEMA,
            "sequence": sequence,
            "previous_entry_sha256": previous_entry["entry_sha256"],
            "baseline_sha256": state["baseline_sha256"],
            "receipt_key_sha256": key_hash,
            "manifest_core_sha256": manifest_core_sha256,
            "authorship": AUTHORSHIP,
        }
        persisted = {**report_data, "receipt_lineage": link}
        receipt_bytes = _json_bytes(persisted)
        receipt_sha256 = _sha256_bytes(receipt_bytes)
        entry = {
            "schema": ENTRY_SCHEMA,
            "entry_type": "RECEIPT",
            "sequence": sequence,
            "previous_entry_sha256": previous_entry["entry_sha256"],
            "baseline_sha256": state["baseline_sha256"],
            "receipt_key_sha256": key_hash,
            "receipt_sha256": receipt_sha256,
            "manifest_core_sha256": manifest_core_sha256,
            "authorship": AUTHORSHIP,
            "entry_sha256": "UNKNOWN",
        }
        entry["entry_sha256"] = _entry_hash(entry)
        _write_bytes_exclusive(path, receipt_bytes)
        _append_journal_entry(journal_path, entry)
        post_errors, _ = _load_and_validate_state(root)
        if post_errors:
            raise RuntimeError(
                "receipt publication left invalid lineage: "
                + "; ".join(sorted(set(post_errors)))
            )
    return requested_path


def lineage_checkpoint(root: Path) -> dict[str, Any]:
    """Return a stable public checkpoint; later valid appends preserve it."""
    root = root.resolve()
    if not lineage_state_exists(root):
        return {
            "schema": "operant-receipt-lineage-checkpoint.v1",
            "baseline_sha256": "UNKNOWN",
            "sequence": "UNKNOWN",
            "entry_sha256": "UNKNOWN",
            "authorship": AUTHORSHIP,
            "history_immutability": HISTORY_IMMUTABILITY,
        }
    errors, state = _load_and_validate_state(root)
    if errors:
        raise RuntimeError(
            "cannot checkpoint invalid receipt lineage: "
            + "; ".join(sorted(set(errors)))
        )
    head = state["entries"][-1]
    return {
        "schema": "operant-receipt-lineage-checkpoint.v1",
        "baseline_sha256": state["baseline_sha256"],
        "sequence": head["sequence"],
        "entry_sha256": head["entry_sha256"],
        "authorship": AUTHORSHIP,
        "history_immutability": HISTORY_IMMUTABILITY,
    }


def validate_lineage_checkpoint(
    root: Path,
    checkpoint: Any,
) -> list[str]:
    """Verify that a public checkpoint remains an ancestor of local state."""
    expected_keys = {
        "schema",
        "baseline_sha256",
        "sequence",
        "entry_sha256",
        "authorship",
        "history_immutability",
    }
    if not isinstance(checkpoint, dict) or set(checkpoint) != expected_keys:
        return ["receipt lineage checkpoint fields are not exact"]
    if (
        checkpoint.get("schema")
        != "operant-receipt-lineage-checkpoint.v1"
        or checkpoint.get("authorship") != AUTHORSHIP
        or checkpoint.get("history_immutability") != HISTORY_IMMUTABILITY
    ):
        return ["receipt lineage checkpoint claims are invalid"]
    sequence = checkpoint.get("sequence")
    if sequence == "UNKNOWN":
        if (
            checkpoint.get("baseline_sha256") != "UNKNOWN"
            or checkpoint.get("entry_sha256") != "UNKNOWN"
        ):
            return ["receipt lineage UNKNOWN checkpoint is inconsistent"]
        return (
            ["receipt lineage checkpoint is UNKNOWN despite local state"]
            if lineage_state_exists(root.resolve())
            else []
        )
    if (
        not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or not 0 <= sequence <= MAX_SEQUENCE
        or not _is_sha256(checkpoint.get("baseline_sha256"))
        or not _is_sha256(checkpoint.get("entry_sha256"))
    ):
        return ["receipt lineage checkpoint values are invalid"]
    errors, state = _load_and_validate_state(root.resolve())
    if errors:
        return errors
    entries = state["entries"]
    if (
        checkpoint["baseline_sha256"] != state["baseline_sha256"]
        or sequence >= len(entries)
        or entries[sequence].get("entry_sha256")
        != checkpoint["entry_sha256"]
    ):
        return ["receipt lineage checkpoint is not a valid ancestor"]
    return []
