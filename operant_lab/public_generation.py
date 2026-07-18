"""Exact generation commit marker for the sanitized public artifact set."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

GENERATION_MANIFEST_NAME = "public-artifact-manifest.json"
GENERATION_SCHEMA = "operant-public-generation-manifest.v1"
GENERATION_COMMIT_SEMANTICS = (
    "This atomically replaced marker binds the exact regular-file set and bytes "
    "of one public export generation, excluding this marker itself."
)
AUTHORSHIP = "UNKNOWN"
EXTERNAL_IMMUTABILITY = "UNKNOWN"
PUBLIC_CORE_FILES = frozenset(
    {
        "README.md",
        "benchmark-card.json",
        "calibration-profiles.json",
        "evaluation-split-registry.json",
        "lab-run-status.json",
        "methodology.md",
    }
)
MANIFEST_KEYS = {
    "schema",
    "artifact_count",
    "artifacts",
    "generation_sha256",
    "commit_semantics",
    "authorship",
    "external_immutability",
}
ARTIFACT_KEYS = {"sha256", "bytes"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MODEL_CARD_RE = re.compile(
    r"^model-cards/[A-Za-z0-9][A-Za-z0-9._-]*\.json$"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _generation_sha256(core: dict[str, Any]) -> str:
    payload = json.dumps(
        core,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@contextmanager
def public_generation_lock(
    public_dir: Path,
    *,
    shared: bool,
    create: bool = False,
) -> Iterator[None]:
    """Serialize writers and hold validators on one directory inode."""
    if public_dir.is_symlink():
        if create:
            raise RuntimeError("public export directory must not be a symlink")
        yield
        return
    if create:
        public_dir.mkdir(parents=True, exist_ok=True)
    if not public_dir.is_dir():
        yield
        return
    directory_fd = os.open(public_dir, os.O_RDONLY)
    try:
        fcntl.flock(
            directory_fd,
            fcntl.LOCK_SH if shared else fcntl.LOCK_EX,
        )
        yield
    finally:
        fcntl.flock(directory_fd, fcntl.LOCK_UN)
        os.close(directory_fd)


def _safe_relative_artifact_key(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in path.parts)
        and (
            value in PUBLIC_CORE_FILES
            or MODEL_CARD_RE.fullmatch(value) is not None
        )
    )


def _inventory_public_files(
    public_dir: Path,
) -> tuple[dict[str, Path], list[str]]:
    files: dict[str, Path] = {}
    errors: list[str] = []
    if not public_dir.exists():
        return files, errors
    for path in sorted(public_dir.rglob("*")):
        relative = path.relative_to(public_dir).as_posix()
        if path.is_symlink():
            errors.append(
                f"generation manifest: symlink is not supported: {relative}"
            )
            continue
        if path.is_dir():
            if relative != "model-cards":
                errors.append(
                    f"generation manifest: unsupported directory: {relative}"
                )
            continue
        if not path.is_file():
            errors.append(
                f"generation manifest: non-regular artifact: {relative}"
            )
            continue
        if relative == GENERATION_MANIFEST_NAME:
            continue
        if not _safe_relative_artifact_key(relative):
            errors.append(
                f"generation manifest: unsupported artifact path: {relative}"
            )
            continue
        files[relative] = path
    return files, errors


def require_supported_public_layout(public_dir: Path) -> None:
    """Refuse to publish over files outside the public artifact contract."""
    _, errors = _inventory_public_files(public_dir)
    if errors:
        raise RuntimeError("; ".join(errors))


def write_text_atomic(path: Path, text: str) -> None:
    payload = text.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=".public-artifact-",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    text = (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    write_text_atomic(path, text)


def write_generation_manifest(public_dir: Path) -> dict[str, Any]:
    """Atomically commit the exact completed public artifact generation."""
    files, errors = _inventory_public_files(public_dir)
    missing = sorted(PUBLIC_CORE_FILES - set(files))
    if missing:
        errors.append(
            "generation manifest: missing core artifacts: " + ", ".join(missing)
        )
    model_cards = [
        key for key in files if MODEL_CARD_RE.fullmatch(key) is not None
    ]
    if not model_cards:
        errors.append("generation manifest: no model cards")
    if errors:
        raise RuntimeError("; ".join(errors))
    artifacts = {
        relative: {
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
        }
        for relative, path in sorted(files.items())
    }
    core = {
        "schema": GENERATION_SCHEMA,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "commit_semantics": GENERATION_COMMIT_SEMANTICS,
        "authorship": AUTHORSHIP,
        "external_immutability": EXTERNAL_IMMUTABILITY,
    }
    manifest = {
        **core,
        "generation_sha256": _generation_sha256(core),
    }
    write_json_atomic(public_dir / GENERATION_MANIFEST_NAME, manifest)
    return manifest


def _validate_generation_manifest(public_dir: Path) -> list[str]:
    errors: list[str] = []
    manifest_path = public_dir / GENERATION_MANIFEST_NAME
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
    ):
        return ["generation manifest: missing regular commit marker"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ["generation manifest: unreadable commit marker"]
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS:
        return ["generation manifest: fields are not exact"]
    if (
        manifest.get("schema") != GENERATION_SCHEMA
        or manifest.get("commit_semantics") != GENERATION_COMMIT_SEMANTICS
        or manifest.get("authorship") != AUTHORSHIP
        or manifest.get("external_immutability") != EXTERNAL_IMMUTABILITY
    ):
        errors.append("generation manifest: contract is invalid")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        return [*errors, "generation manifest: artifacts must be an object"]
    artifacts_are_safe = all(
        _safe_relative_artifact_key(key)
        and isinstance(value, dict)
        and set(value) == ARTIFACT_KEYS
        and isinstance(value.get("sha256"), str)
        and SHA256_RE.fullmatch(value["sha256"]) is not None
        and isinstance(value.get("bytes"), int)
        and not isinstance(value.get("bytes"), bool)
        and 0 <= value["bytes"] <= 9_007_199_254_740_991
        for key, value in artifacts.items()
    )
    if not artifacts_are_safe:
        errors.append("generation manifest: artifact map is unsafe")
    count = manifest.get("artifact_count")
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or count != len(artifacts)
    ):
        errors.append("generation manifest: artifact count is invalid")
    if artifacts_are_safe:
        core = {
            key: manifest[key]
            for key in MANIFEST_KEYS
            if key != "generation_sha256"
        }
        expected_generation_sha256 = _generation_sha256(core)
        if manifest.get("generation_sha256") != expected_generation_sha256:
            errors.append("generation manifest: generation digest mismatch")
    files, inventory_errors = _inventory_public_files(public_dir)
    errors.extend(inventory_errors)
    if set(files) != set(artifacts):
        errors.append("generation manifest: exact artifact set mismatch")
    for relative in sorted(set(files) & set(artifacts)):
        try:
            expected = artifacts[relative]
            if not isinstance(expected, dict):
                continue
            if (
                files[relative].stat().st_size != expected.get("bytes")
                or _sha256(files[relative]) != expected.get("sha256")
            ):
                errors.append(
                    f"generation manifest: artifact bytes changed: {relative}"
                )
        except OSError:
            errors.append(
                f"generation manifest: artifact became unreadable: {relative}"
            )
    return errors


def validate_generation_manifest(public_dir: Path) -> list[str]:
    """Validate exact file membership and bytes without raising."""
    try:
        return _validate_generation_manifest(public_dir)
    except (
        AttributeError,
        KeyError,
        OSError,
        OverflowError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        return ["generation manifest: invalid or unstable artifact state"]
