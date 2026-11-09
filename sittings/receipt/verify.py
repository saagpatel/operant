"""Verify signed claims and re-hash their supporting bytes."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from checkseal.dsse import PAYLOAD_TYPE, Envelope
from checkseal.sign.local import LocalKeyVerifier
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from sittings.canonical import canonical_bytes, canonical_sha256, sha256_hex
from .sign import PREDICATE_TYPE, RECEIPT_FORMAT, SEAL_BINDS, SEAL_DOES_NOT_BIND


@dataclass
class Report:
    findings: list[dict] = field(default_factory=list)

    @property
    def ok(self):
        return bool(self.findings) and all(f["ok"] for f in self.findings)

    def add(self, name, ok, detail):
        self.findings.append({"field": name, "ok": bool(ok), "detail": detail})


def _manifest(manifest, blob_root, repo_root):
    files = manifest["files"]
    if not isinstance(files, list) or not files:
        raise ValueError("empty file list")
    paths = [f["path"] for f in files]
    if paths != sorted(set(paths)):
        raise ValueError("paths must be sorted and unique")
    if (
        canonical_sha256([{"path": f["path"], "sha256": f["sha256"]} for f in files])
        != manifest["sha256"]
    ):
        raise ValueError("aggregate mismatch")
    found = {}
    for f in files:
        path, digest = f["path"], f["sha256"]
        parts = PurePosixPath(path)
        if (
            parts.is_absolute()
            or ".." in parts.parts
            or str(parts) != path
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise ValueError("invalid path or digest")
        candidates = []
        if blob_root is not None:
            candidates.append(Path(blob_root) / digest)
        if repo_root is not None:
            root = Path(repo_root).resolve()
            candidate = (root / path).resolve()
            if not candidate.is_relative_to(root):
                raise ValueError("path escapes repository")
            candidates.append(candidate)
        data = next(
            (
                p.read_bytes()
                for p in candidates
                if p.is_file() and sha256_hex(p.read_bytes()) == digest
            ),
            None,
        )
        if data is None or len(data) != f["bytes"]:
            raise ValueError(f"bytes unavailable or mismatched: {path}")
        found[path] = data
    return found


def verify_receipt(receipt, *, pubkeys, record=None, blob_root=None, repo_root=None, rescore=True):
    report = Report()
    try:
        envelope = Envelope.from_jsonable(receipt["envelope"])
        statement = json.loads(envelope.payload)
        pred = statement["predicate"]
        valid = (
            receipt["receipt_format"] == RECEIPT_FORMAT
            and pred["receipt_format"] == RECEIPT_FORMAT
            and envelope.payload_type == PAYLOAD_TYPE
            and canonical_bytes(statement) == envelope.payload
            and statement["_type"] == "https://in-toto.io/Statement/v1"
            and statement["predicateType"] == PREDICATE_TYPE
            and pred["seal_binds"] == SEAL_BINDS
            and pred["seal_does_not_bind"] == SEAL_DOES_NOT_BIND
        )
        report.add(
            "format", valid, "known canonical format" if valid else "unknown or malformed format"
        )
    except Exception as exc:
        report.add("format", False, f"malformed receipt: {exc}")
        return report
    try:
        if (
            not valid
            or pubkeys["format"] != "operant.sittings-pubkey/v1"
            or len(envelope.signatures) != 1
        ):
            raise ValueError("invalid format or signature count")
        keyid = receipt["keyid"]
        entries = [k for k in pubkeys["keys"] if k["keyid"] == keyid and k["alg"] == "ed25519"]
        if len(entries) != 1 or envelope.signatures[0].keyid != keyid:
            raise ValueError("untrusted or mismatched keyid")
        key = serialization.load_pem_public_key(entries[0]["public_key_pem"].encode())
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("not an Ed25519 key")
        der = key.public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        if base64.b64encode(der).decode() != entries[0]["spki_der_b64"]:
            raise ValueError("public key encodings disagree")
        LocalKeyVerifier(key).verify(envelope.signing_bytes(), envelope.signatures[0])
        report.add("signature", True, "trusted signature valid")
    except Exception as exc:
        report.add("signature", False, str(exc))
    try:
        subject = statement["subject"]
        matches = (
            record is not None
            and len(subject) == 1
            and subject[0]["name"] == "operant-sitting:" + pred["sitting_id"]
            and subject[0]["digest"]["sha256"] == canonical_sha256(record)
            and all(
                pred[k] == record[k]
                for k in (
                    "sitting_id",
                    "agent_label",
                    "claimed_model",
                    "corpus",
                    "scorer",
                    "score",
                    "model_binding",
                )
            )
        )
        report.add(
            "subject", matches, "record matches" if matches else "record missing or does not match"
        )
        answers_ok = record is not None and pred["answers_sha256"] == canonical_sha256(
            record["answers"]
        )
        report.add(
            "answers_sha256",
            answers_ok,
            "answers match" if answers_ok else "answers missing or mismatch",
        )
    except Exception as exc:
        report.add("subject", False, str(exc))
        report.add("answers_sha256", False, str(exc))
    manifests = {}
    for name in ("corpus", "scorer"):
        try:
            manifests[name] = _manifest(pred[name], blob_root, repo_root)
            report.add(name, True, "aggregate and file bytes match")
        except Exception as exc:
            report.add(name, False, str(exc))
    if rescore:
        # Fail closed until the bound-scoring loader can be isolated from all unbound imports.
        report.add("score", False, "bound scorer isolation unavailable")
    else:
        report.add("score", False, "rescoring disabled")
    try:
        from sittings.model_binding import verify_binding
    except ImportError:
        report.add("model_binding", False, "binding verifier unavailable")
    else:
        try:
            findings = verify_binding(pred["model_binding"], record)
            if not findings:
                raise ValueError("binding verifier returned no findings")
            report.findings.extend(findings)
        except Exception as exc:
            report.add("model_binding", False, str(exc))
    return report
