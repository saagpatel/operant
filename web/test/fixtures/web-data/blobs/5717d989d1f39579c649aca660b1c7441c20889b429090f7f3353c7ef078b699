"""Canonical JSON and hashing shared by every sittings module and the browser verifier."""

from __future__ import annotations

import hashlib
import json
from typing import Any

MAX_SAFE_INTEGER = (1 << 53) - 1


def _validate(value: Any) -> None:
    if value is None or type(value) in (str, bool):
        return
    if type(value) is int:
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            raise ValueError("integer outside interoperable safe range")
        return
    if type(value) is float:
        raise ValueError("floats are not canonical values")
    if isinstance(value, list):
        for item in value:
            _validate(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key.isascii():
                raise ValueError("object keys must be ASCII strings")
            _validate(item)
        return
    raise ValueError(f"unsupported canonical value: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    """Return stable JSON bytes after rejecting non-portable JSON values."""
    _validate(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_sha256(value: Any) -> str:
    return sha256_hex(canonical_bytes(value))
