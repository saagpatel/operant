"""Keep the public v1 binding policy fixture aligned with the Python producer."""
import json
import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch

from sittings.model_binding import CALL_FIELDS, FORMAT, POLICY, _build_binding


ROOT = Path(__file__).resolve().parent
POLICY_PATH = ROOT / "sittings" / "testdata" / "model-binding-policy-v1.json"


def python_policy():
    record = {"sitting_id": "sit_test", "claimed_model": "model-a", "case_order": [], "answers": []}
    binding = _build_binding(record, [], None)
    return {
        "format": FORMAT,
        "binding_fields": sorted(binding),
        "levels": {
            level: {
                "binds": list(binds),
                "does_not_bind": list(limits),
                "evidence_fields": sorted(binding["evidence"]),
            }
            for level, (binds, limits) in POLICY.items()
        },
        "call_fields": sorted(CALL_FIELDS),
        "claim_status": {
            "unknown": "observed_model is null",
            "match": "claimed_model equals observed_model",
            "mismatch": "claimed_model differs from observed_model",
        },
        "observed_model": "the sole distinct nonempty response_model across calls, otherwise null",
    }


class BindingPolicyTests(unittest.TestCase):
    def test_fixture_matches_python_constants_and_producer_schema(self):
        self.assertEqual(json.loads(POLICY_PATH.read_text(encoding="utf-8")), python_policy())

    def test_observed_model_and_claim_status_are_derived_for_unbound_evidence(self):
        cases = [
            ([], "model-a", None, "unknown"),
            ([None], "model-a", None, "unknown"),
            ([""], "model-a", None, "unknown"),
            (["model-a"], "model-a", "model-a", "match"),
            (["model-a"], "model-b", "model-a", "mismatch"),
            (["model-a", "model-a"], "model-a", "model-a", "match"),
            (["model-a", "model-b"], "model-a", None, "unknown"),
            ([None, "model-a"], "model-a", "model-a", "match"),
            (["", "model-a"], "model-b", "model-a", "mismatch"),
        ]
        for models, claimed, expected_observed, expected_status in cases:
            with self.subTest(models=models, claimed=claimed):
                record = {
                    "sitting_id": "sit_test",
                    "claimed_model": claimed,
                    "case_order": [f"case-{index}" for index in range(len(models))],
                    "answers": [],
                }
                calls = [
                    {
                        "call_id": f"call_{index:032x}",
                        "case_id": f"case-{index}",
                        "provider": "provider",
                        "upstream_base_url": "https://provider.example",
                        "requested_model": "requested-model",
                        "response_model": model,
                        "response_header_model": None,
                        "completion_text": "answer",
                    }
                    for index, model in enumerate(models)
                ]
                with patch("sittings.model_binding._validate_call", return_value="prompt"):
                    binding = _build_binding(record, calls, None)
                self.assertEqual(binding["level"], "unbound")
                self.assertEqual(binding["observed_model"], expected_observed)
                self.assertEqual(binding["evidence"]["claim_status"], expected_status)

    def test_complete_relay_derives_host_relayed_level_and_claim(self):
        call = {
            "call_id": "call_00000000000000000000000000000001",
            "case_id": "case-1",
            "provider": "provider",
            "upstream_base_url": "https://provider.example",
            "requested_model": "model-a",
            "response_model": "model-a",
            "response_header_model": None,
            "completion_text": "answer",
        }
        record = {
            "sitting_id": "sit_test",
            "claimed_model": "model-a",
            "case_order": ["case-1"],
            "answers": [{
                "case_id": "case-1",
                "answer": "answer",
                "prompt_sha256": hashlib.sha256(b"prompt").hexdigest(),
                "relay_call_ids": [call["call_id"]],
            }],
        }
        with patch("sittings.model_binding._validate_call", return_value="prompt"):
            binding = _build_binding(record, [call], {"case-1": "prompt"})
        self.assertEqual(binding["level"], "host-relayed")
        self.assertEqual(binding["observed_model"], "model-a")
        self.assertEqual(binding["evidence"]["claim_status"], "match")


if __name__ == "__main__":
    unittest.main()
