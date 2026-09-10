from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from operant_lab.preregistration import (
    build_preregistration,
    load_verified_preregistration,
    validate_preregistration,
    write_preregistration,
)


def _spec(**overrides):
    spec = {
        "experiment_id": "fixture-r1",
        "research_question": "Does the declared treatment change guarded-case discrimination?",
        "hypotheses": ["The treatment will not be interpreted as a model ranking."],
        "primary_metrics": ["ocs", "decision_accuracy"],
        "exclusions": ["Exclude only attempts rejected by the predeclared parse contract."],
        "stopping_rule": "Run the complete declared matrix once; stop on any missing cell.",
        "analysis_plan": (
            "Report paired differences and preserve failed attempts; "
            "do not rank named models."
        ),
        "bound_inputs": [
            {"name": "cases", "sha256": "a" * 64, "count": 40},
            {"name": "protocol", "sha256": "b" * 64},
            {"name": "subject", "sha256": "c" * 64},
            {"name": "dependencies", "sha256": "d" * 64},
        ],
        "execution_policy": {
            "max_retries": 0,
            "network_policy": "DISABLED",
            "spend_policy": "NO_PAID_MODEL_OR_API_SPEND",
            "tool_policy": "NO_TOOLS",
        },
        "claim_ceiling": "This is a local non-confirmatory diagnostic over the bound bytes.",
        "prohibited_claims": [
            "No served-model identity, causal effect, certification, or external replication claim."
        ],
    }
    spec.update(overrides)
    return spec


class PreregistrationTests(unittest.TestCase):
    def test_build_fixes_non_confirmatory_admission(self):
        registration = build_preregistration(
            _spec(), registered_at="2026-09-10T00:00:00Z"
        )
        self.assertEqual(registration["schema"], "operant-experiment-preregistration.v1")
        self.assertEqual(registration["status"], "REGISTERED_NOT_EXECUTED")
        self.assertFalse(registration["admission"]["confirmatory_eligible"])
        self.assertEqual(validate_preregistration(registration), [])

    def test_spec_cannot_override_admission_or_smuggle_results(self):
        with self.assertRaisesRegex(ValueError, "unsupported keys"):
            build_preregistration(_spec(admission={}))
        with self.assertRaisesRegex(ValueError, "result-bearing key"):
            build_preregistration(_spec(analysis_plan={"score": 0.9}))

    def test_invalid_identity_and_paid_spend_are_rejected(self):
        errors = validate_preregistration(
            build_preregistration(_spec()) | {
                "execution_policy": {
                    "max_retries": 1,
                    "network_policy": "DISABLED",
                    "spend_policy": "PAID",
                    "tool_policy": "NO_TOOLS",
                },
            }
        )
        self.assertTrue(any("max_retries" in error for error in errors))
        self.assertTrue(any("spend_policy" in error for error in errors))

    def test_registration_rejects_unknown_top_level_fields(self):
        registration = build_preregistration(_spec())
        errors = validate_preregistration({**registration, "observed_score": 1.0})
        self.assertTrue(any("unsupported keys" in error for error in errors))

    def test_registration_requires_core_identity_bindings(self):
        spec = _spec()
        spec["bound_inputs"] = [
            row for row in spec["bound_inputs"] if row["name"] != "subject"
        ]
        with self.assertRaisesRegex(ValueError, "missing required identities"):
            build_preregistration(spec)

    def test_digest_is_canonical_and_write_is_exclusive(self):
        registration = build_preregistration(
            _spec(), registered_at="2026-09-10T00:00:00Z"
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.json"
            digest, sidecar = write_preregistration(registration, path)
            self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(sidecar.read_text(), f"{digest}  fixture.json\n")
            loaded, loaded_digest = load_verified_preregistration(path)
            self.assertEqual(loaded, registration)
            self.assertEqual(loaded_digest, digest)
            with self.assertRaises(FileExistsError):
                write_preregistration(registration, path)

    def test_tampered_registration_fails_closed(self):
        registration = build_preregistration(_spec())
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.json"
            write_preregistration(registration, path)
            path.write_text(path.read_text().replace("fixture-r1", "tampered-r1"))
            with self.assertRaisesRegex(ValueError, "sidecar mismatch"):
                load_verified_preregistration(path)


if __name__ == "__main__":
    unittest.main()
