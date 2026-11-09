"""Verify signed sitting receipts and replay trusted scorer releases in isolation."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from checkseal.dsse import PAYLOAD_TYPE, Envelope, pae
from checkseal.sign.local import LocalKeyVerifier
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from sittings.canonical import canonical_bytes, canonical_sha256, sha256_hex
from .sign import PREDICATE_TYPE, RECEIPT_FORMAT, SEAL_BINDS, SEAL_DOES_NOT_BIND

KEY_ID = re.compile(r"ed25519:[0-9a-f]{16}")
DIGEST = re.compile(r"[0-9a-f]{64}")
TRUSTED_SCORERS = Path(__file__).resolve().parents[1] / "trusted_scorers.json"


@dataclass
class Report:
    findings: list[dict] = field(default_factory=list)

    @property
    def ok(self):
        return bool(self.findings) and all(f["ok"] for f in self.findings)

    def add(self, name, ok, detail):
        self.findings.append({"field": name, "ok": bool(ok), "detail": detail})


def _trusted_hashes(path: Path = TRUSTED_SCORERS) -> set[str]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("format") != "operant.trusted-scorers/v1" or not isinstance(doc.get("sha256"), list):
        raise ValueError("invalid trusted scorer releases")
    values = doc["sha256"]
    if any(not isinstance(value, str) or not DIGEST.fullmatch(value) for value in values):
        raise ValueError("invalid trusted scorer digest")
    return set(values)


def _safe_manifest(manifest):
    if not isinstance(manifest, dict) or set(manifest) != {"sha256", "files"}:
        raise ValueError("invalid manifest shape")
    if not isinstance(manifest["sha256"], str) or not DIGEST.fullmatch(manifest["sha256"]):
        raise ValueError("invalid aggregate digest")
    files = manifest["files"]
    if not isinstance(files, list) or not files:
        raise ValueError("empty file list")
    paths = []
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "bytes"}:
            raise ValueError("invalid file entry")
        path, digest, size = item["path"], item["sha256"], item["bytes"]
        if not isinstance(path, str) or not path or "\\" in path:
            raise ValueError("invalid path")
        raw_parts = path.split("/")
        parts = PurePosixPath(path)
        if (
            parts.is_absolute() or ".." in raw_parts or "." in raw_parts
            or any(not part or ":" in part for part in raw_parts)
            or str(parts) != path
        ):
            raise ValueError("invalid path")
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise ValueError("invalid digest")
        if type(size) is not int or size < 0:
            raise ValueError("invalid byte count")
        paths.append(path)
    if paths != sorted(set(paths)):
        raise ValueError("paths must be sorted and unique")
    aggregate = canonical_sha256([{"path": x["path"], "sha256": x["sha256"]} for x in files])
    if aggregate != manifest["sha256"]:
        raise ValueError("aggregate mismatch")
    return files


def _read_manifest(manifest, blob_root, repo_root):
    files = _safe_manifest(manifest)
    found = {}
    for item in files:
        path, digest = item["path"], item["sha256"]
        data = None
        if blob_root is not None:
            blob_base = Path(blob_root).absolute()
            candidate = blob_base / digest
            cursor = Path(blob_base.anchor)
            for part in blob_base.parts[1:]:
                cursor = cursor / part
                if cursor.is_symlink():
                    raise ValueError("symlink refused")
            if candidate.is_symlink():
                raise ValueError("symlink refused")
            if candidate.is_file():
                candidate_data = candidate.read_bytes()
                if sha256_hex(candidate_data) == digest:
                    data = candidate_data
        if data is None and repo_root is not None:
            repo_base = Path(repo_root).absolute()
            cursor = Path(repo_base.anchor)
            for part in repo_base.parts[1:]:
                cursor = cursor / part
                if cursor.is_symlink():
                    raise ValueError("symlink refused")
            root = repo_base.resolve()
            candidate = repo_base / PurePosixPath(path)
            cursor = repo_base
            for part in PurePosixPath(path).parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    raise ValueError("symlink refused")
            resolved = candidate.resolve()
            if not resolved.is_relative_to(root):
                raise ValueError("path escapes repository")
            candidate_data = candidate.read_bytes()
            if sha256_hex(candidate_data) == digest:
                data = candidate_data
        if data is None or len(data) != item["bytes"]:
            raise ValueError(f"bytes unavailable or mismatched: {path}")
        found[path] = data
    return found


def _key_from_entry(entry):
    key = serialization.load_pem_public_key(entry["public_key_pem"].encode("ascii"))
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("not an Ed25519 key")
    der = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    expected = "ed25519:" + hashlib.sha256(raw).hexdigest()[:16]
    if entry.get("keyid") != expected or base64.b64encode(der).decode("ascii") != entry.get("spki_der_b64"):
        raise ValueError("trusted key id or encodings disagree")
    return key, expected


def _rescore(record, corpus_bytes, scorer_bytes, scorer_hash, trusted_scorers=TRUSTED_SCORERS):
    trusted = _trusted_hashes(Path(trusted_scorers))
    if scorer_hash not in trusted:
        return False, "scorer release not trusted; not executed"
    with tempfile.TemporaryDirectory(prefix="operant-rescore-") as temp:
        root = Path(temp)
        case_bytes = {
            path: data for path, data in corpus_bytes.items()
            if path.endswith("_cases.json") and path != "operant_axis3_cases.json"
        }
        for group in (case_bytes, scorer_bytes):
            for relative, data in group.items():
                target = root / PurePosixPath(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        script = (
            "import json,sys; from pathlib import Path; "
            "sys.path.insert(0, sys.argv[1]); "
            "from sittings.scoring import load_corpus,score_answers; "
            "from sittings.canonical import canonical_bytes; "
            "r=json.load(sys.stdin); "
            "score=score_answers(load_corpus(),r['answers'], "
            "non_publishable_reasons=r['extra_reasons']); "
            "sys.stdout.buffer.write(canonical_bytes(score))"
        )
        extra_reasons = _runner_reasons(record)
        completed = subprocess.run(
            [sys.executable, "-I", "-c", script, str(root)],
            input=canonical_bytes({"answers": record["answers"], "extra_reasons": extra_reasons}),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=root,
            timeout=30,
            check=False,
        )
        if completed.returncode:
            return False, "isolated bound scorer failed"
        try:
            expected = canonical_bytes(record["score"])
            actual = completed.stdout
        except (TypeError, ValueError):
            return False, "record score is not canonical"
        return actual == expected, "score matches isolated trusted scorer" if actual == expected else "score mismatch"


def _runner_reasons(record):
    """Allow the fixed runner admission reasons, checking relay claims against binding."""
    score = record["score"]
    reasons = score.get("not_publishable_reasons")
    if not isinstance(reasons, list) or any(not isinstance(reason, str) for reason in reasons):
        raise ValueError("invalid non-publishable reasons")
    binding = record.get("model_binding", {})
    evidence = binding.get("evidence", {}) if isinstance(binding, dict) else {}
    status = evidence.get("claim_status") if isinstance(evidence, dict) else None
    level = binding.get("level") if isinstance(binding, dict) else None
    calls = evidence.get("calls", []) if isinstance(evidence, dict) else []
    allowed = {"bound files changed during sitting"}
    if status == "mismatch":
        allowed.add("claimed model does not match observed model")
    if calls and status == "unknown" and level != "host-relayed":
        allowed.add("provider did not report an observed model")
    if calls and level != "host-relayed":
        allowed.add("host relay did not bind every answer to one consistent model")
    # Host reasons are the recorded reasons in this allowlist, in recorded order.
    # The scorer independently reconstructs all other reasons during replay.
    return [reason for reason in reasons if reason in allowed]


def verify_receipt(
    receipt, *, pubkeys, record=None, blob_root=None, repo_root=None, rescore=True,
    trusted_scorers=TRUSTED_SCORERS,
    trusted_corpora=(),
):
    """Verify in amendment A6 order: parse, key agreement, signature, then claims.

    trusted_corpora lists additional corpus hashes accepted for live prompt checks.
    """
    report = Report()
    try:
        envelope = Envelope.from_jsonable(receipt["envelope"])
        statement = json.loads(envelope.payload)
        pred = statement["predicate"]
        if not isinstance(receipt, dict) or not isinstance(statement, dict) or not isinstance(pred, dict):
            raise ValueError("receipt payload must contain objects")
    except Exception as exc:
        report.add("parse", False, f"malformed receipt: {type(exc).__name__}")
        return report
    report.add("parse", True, "receipt and raw payload parsed")

    try:
        if not isinstance(pubkeys, dict) or pubkeys.get("format") != "operant.sittings-pubkey/v1":
            raise ValueError("invalid trusted key document")
        if not isinstance(receipt.get("keyid"), str) or not KEY_ID.fullmatch(receipt["keyid"]):
            raise ValueError("invalid wrapper keyid")
        if len(envelope.signatures) != 1 or envelope.signatures[0].keyid != receipt["keyid"]:
            raise ValueError("wrapper and signature keyids disagree")
        entries = [x for x in pubkeys.get("keys", []) if isinstance(x, dict) and x.get("keyid") == receipt["keyid"]]
        if len(entries) != 1 or entries[0].get("alg") != "ed25519":
            raise ValueError("untrusted keyid")
        key, keyid = _key_from_entry(entries[0])
        if keyid != receipt["keyid"]:
            raise ValueError("trusted keyid mismatch")
        report.add("keyid", True, "wrapper, signature, and trusted CheckSeal keyid agree")
    except Exception as exc:
        report.add("keyid", False, str(exc))
        return report

    try:
        # Verify the signature over the decoded payload bytes without re-serialization.
        LocalKeyVerifier(key).verify(pae(envelope.payload_type, envelope.payload), envelope.signatures[0])
        report.add("signature", True, "trusted Ed25519 signature valid over raw payload")
    except Exception as exc:
        report.add("signature", False, str(exc))
        return report

    try:
        valid_format = (
            receipt.get("receipt_format") == RECEIPT_FORMAT
            and pred.get("receipt_format") == RECEIPT_FORMAT
            and envelope.payload_type == PAYLOAD_TYPE
            and canonical_bytes(statement) == envelope.payload
            and statement.get("_type") == "https://in-toto.io/Statement/v1"
            and statement.get("predicateType") == PREDICATE_TYPE
            and pred.get("seal_binds") == SEAL_BINDS
            and pred.get("seal_does_not_bind") == SEAL_DOES_NOT_BIND
        )
    except (TypeError, ValueError):
        valid_format = False
    report.add("format", valid_format, "known canonical format" if valid_format else "unknown or malformed format")

    try:
        subject = statement["subject"]
        matches = (
            valid_format and record is not None and len(subject) == 1
            and subject[0]["name"] == "operant-sitting:" + pred["sitting_id"]
            and subject[0]["digest"]["sha256"] == canonical_sha256(record)
            and all(pred[k] == record[k] for k in ("sitting_id", "agent_label", "claimed_model", "corpus", "scorer", "score", "model_binding"))
        )
        report.add("subject", matches, "record matches" if matches else "record missing or does not match")
        answers_ok = record is not None and pred["answers_sha256"] == canonical_sha256(record["answers"])
        report.add("answers_sha256", answers_ok, "answers match" if answers_ok else "answers missing or mismatch")
    except Exception as exc:
        report.add("subject", False, str(exc))
        report.add("answers_sha256", False, str(exc))
        matches = False
        answers_ok = False

    manifests = {}
    for name in ("corpus", "scorer"):
        try:
            manifests[name] = _read_manifest(pred[name], blob_root, repo_root)
            report.add(name, True, "aggregate and file bytes match")
        except Exception as exc:
            report.add(name, False, str(exc))
    if (
        rescore and valid_format and matches and answers_ok and record is not None
        and "corpus" in manifests and "scorer" in manifests
    ):
        try:
            ok, detail = _rescore(
                record, manifests["corpus"], manifests["scorer"],
                pred["scorer"]["sha256"], trusted_scorers,
            )
            report.add("score", ok, detail)
        except Exception as exc:
            report.add("score", False, str(exc))
    elif not rescore:
        report.add("score", False, "rescoring disabled")
    else:
        report.add("score", False, "rescore skipped because format, subject, or answers failed")
    try:
        from sittings.model_binding import verify_binding
        findings = verify_binding(
            pred["model_binding"], record,
            corpus_bytes=manifests.get("corpus", {}), trusted_corpora=trusted_corpora,
        )
        if not findings:
            raise ValueError("binding verifier returned no findings")
        report.findings.extend(findings)
    except ImportError:
        report.add("model_binding", False, "binding verifier unavailable")
    except Exception as exc:
        report.add("model_binding", False, str(exc))
    if record is not None:
        try:
            report.add("publishability", *_verify_admission(record))
        except Exception as exc:
            report.add("publishability", False, str(exc))
    return report


def _verify_admission(record):
    """Recheck host-relay admission reasons and qualify the stale-bytes assertion."""
    score = record["score"]
    reasons = score["not_publishable_reasons"]
    binding = record["model_binding"]
    evidence = binding["evidence"]
    calls = evidence["calls"]
    required = []
    if calls and binding["level"] != "host-relayed":
        required.append("host relay did not bind every answer to one consistent model")
    if calls and binding.get("observed_model") is None:
        required.append("provider did not report an observed model")
    if evidence.get("claim_status") == "mismatch":
        required.append("claimed model does not match observed model")
    missing = [reason for reason in required if reason not in reasons]
    if missing or (required and score.get("publishable") is not False):
        return False, "host-relay admission reason missing or marked publishable"
    if "bound files changed during sitting" in reasons:
        return True, "signed host assertion reproduced; the historical change cannot be independently reconstructed"
    return True, "publishability agrees with scorer and host-relay binding evidence"
