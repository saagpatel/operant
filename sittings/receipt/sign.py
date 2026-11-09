"""Create T1 local-key sitting receipts."""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime
from pathlib import Path

from checkseal.dsse import PAYLOAD_TYPE, Envelope, pae
from checkseal.sign.local import (
    LocalKeySigner,
    load_private_signer,
    write_private_pem,
    write_public_pem,
)
from cryptography.hazmat.primitives import serialization

from sittings.canonical import canonical_bytes, canonical_sha256

RECEIPT_FORMAT = "operant.sitting-receipt/v1"
PREDICATE_TYPE = "https://operant.saagarpatel.dev/sitting-receipt/v1"
SEAL_BINDS = [
    "corpus bytes",
    "scorer bytes",
    "the answers",
    "the score recomputed from them",
    "the model binding at its stated level",
]
SEAL_DOES_NOT_BIND = [
    "the submitter's honesty about their setup beyond the model binding",
    "that the agent was not tuned on the public corpus",
    "anything about the orchestration axis",
    "the identity of the submitter",
    "that the T1 local host signing key was not compromised",
]


def load_signer(path):
    return load_private_signer(str(path))


def generate_key(out_priv, out_pub):
    signer = LocalKeySigner.generate()
    fd = os.open(out_priv, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        pub_fd = os.open(out_pub, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        os.close(pub_fd)
    except Exception:
        Path(out_priv).unlink(missing_ok=True)
        raise
    try:
        write_private_pem(signer, str(out_priv))
        write_public_pem(signer, str(out_pub))
    except Exception:
        Path(out_priv).unlink(missing_ok=True)
        Path(out_pub).unlink(missing_ok=True)
        raise
    return signer


def export_pubkey_json(signer):
    key = signer.public_key
    pem = key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    der = key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return {
        "format": "operant.sittings-pubkey/v1",
        "keys": [
            {
                "keyid": signer.keyid,
                "alg": "ed25519",
                "public_key_pem": pem.decode("ascii"),
                "spki_der_b64": base64.b64encode(der).decode("ascii"),
            }
        ],
    }


def seal_record(record, signer):
    predicate = {
        key: record[key]
        for key in (
            "sitting_id",
            "agent_label",
            "claimed_model",
            "corpus",
            "scorer",
            "score",
            "model_binding",
        )
    }
    predicate.update(
        receipt_format=RECEIPT_FORMAT,
        sealed_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        answers_sha256=canonical_sha256(record["answers"]),
        seal_binds=SEAL_BINDS,
        seal_does_not_bind=SEAL_DOES_NOT_BIND,
    )
    statement = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [
            {
                "name": "operant-sitting:" + record["sitting_id"],
                "digest": {"sha256": canonical_sha256(record)},
            }
        ],
        "predicateType": PREDICATE_TYPE,
        "predicate": predicate,
    }
    payload = canonical_bytes(statement)
    envelope = Envelope(
        payload=payload,
        payload_type=PAYLOAD_TYPE,
        signatures=[signer.sign(pae(PAYLOAD_TYPE, payload))],
    )
    return {
        "receipt_format": RECEIPT_FORMAT,
        "keyid": signer.keyid,
        "envelope": envelope.to_jsonable(),
    }
