"""Tests for the implemented receipt integrity checks."""

import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path

from checkseal.sign.local import LocalKeySigner

from sittings.manifest import files_manifest
from sittings.receipt import export_pubkey_json, seal_record, verify_receipt


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "corpus.json").write_text("{}")
        (self.root / "scorer.py").write_text("# scorer\n")
        self.record = {
            "format": "operant.sitting/v1",
            "sitting_id": "sit_example",
            "agent_label": "agent",
            "claimed_model": "model",
            "answers": [{"case_id": "a", "answer": "PROCEED"}],
            "corpus": files_manifest(["corpus.json"], self.root),
            "scorer": files_manifest(["scorer.py"], self.root),
            "score": {"ocs": 0},
            "model_binding": {"level": "unbound"},
        }
        self.signer = LocalKeySigner.generate()
        self.keys = export_pubkey_json(self.signer)
        self.receipt = seal_record(self.record, self.signer)

    def findings(self, receipt=None, record=None, keys=None):
        result = verify_receipt(
            receipt or self.receipt,
            pubkeys=keys or self.keys,
            record=record or self.record,
            repo_root=self.root,
        )
        return {f["field"]: f for f in result.findings}

    def test_integrity_fields_and_unavailable_integrations(self):
        findings = self.findings()
        for name in ("format", "signature", "subject", "answers_sha256", "corpus", "scorer"):
            self.assertTrue(findings[name]["ok"], findings[name])
        self.assertFalse(findings["score"]["ok"])
        if importlib.util.find_spec("sittings.model_binding") is None:
            self.assertEqual(findings["model_binding"]["detail"], "binding verifier unavailable")

    def test_tampered_record_and_bytes(self):
        record = copy.deepcopy(self.record)
        record["answers"][0]["answer"] = "REFUSE"
        findings = self.findings(record=record)
        self.assertFalse(findings["subject"]["ok"])
        self.assertFalse(findings["answers_sha256"]["ok"])
        (self.root / "corpus.json").write_text("changed")
        self.assertFalse(self.findings()["corpus"]["ok"])
        (self.root / "scorer.py").write_text("changed")
        self.assertFalse(self.findings()["scorer"]["ok"])

    def test_untrusted_key_and_signature(self):
        other_keys = export_pubkey_json(LocalKeySigner.generate())
        self.assertFalse(self.findings(keys=other_keys)["signature"]["ok"])
        receipt = copy.deepcopy(self.receipt)
        receipt["envelope"]["signatures"][0]["sig"] = "AAAA"
        self.assertFalse(self.findings(receipt=receipt)["signature"]["ok"])
        receipt = copy.deepcopy(self.receipt)
        receipt["receipt_format"] = "unknown"
        self.assertFalse(self.findings(receipt=receipt)["format"]["ok"])


if __name__ == "__main__":
    unittest.main()
