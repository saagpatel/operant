#!/usr/bin/env python3
"""Adversarial tests for the OPERANT evaluation split contract."""

from __future__ import annotations

import copy
import shutil
import tempfile
import unittest
from pathlib import Path

import gen_cases
import verify_evaluation_split as verifier
from operant_lab.public_contract import validate_public_artifacts


class EvaluationSplitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = verifier._read_json(verifier.REGISTRY)

    def test_current_registry_verifies(self) -> None:
        self.assertEqual(verifier.verify(self.registry), [])

    def test_confirmatory_status_cannot_be_upgraded(self) -> None:
        changed = copy.deepcopy(self.registry)
        changed["current_confirmatory_status"] = "ESTABLISHED"
        self.assertIn(
            "confirmatory status must remain NOT_ESTABLISHED",
            verifier.verify(changed),
        )

    def test_confirmatory_claim_cannot_be_enabled(self) -> None:
        changed = copy.deepcopy(self.registry)
        changed["confirmatory_claim_allowed"] = True
        self.assertIn(
            "confirmatory claims must remain prohibited",
            verifier.verify(changed),
        )

    def test_private_surface_holdout_cannot_be_promoted(self) -> None:
        changed = copy.deepcopy(self.registry)
        private = next(
            row
            for row in changed["split_dispositions"]
            if row["split_id"] == "generated-private"
        )
        private["confirmatory_eligible"] = True
        private["structural_independence"] = "YES"
        errors = verifier.verify(changed)
        self.assertIn(
            "generated-private: unavailable confirmatory evidence upgraded",
            errors,
        )
        self.assertIn("generated-private: split classification changed", errors)

    def test_adaptive_followup_cannot_be_reclassified(self) -> None:
        changed = copy.deepcopy(self.registry)
        changed["run_family_dispositions"][
            "codex-gpt55-sanctioned-path-followup"
        ] = "CONFIRMATORY"
        self.assertIn(
            "run-family evaluation roles changed",
            verifier.verify(changed),
        )

    def test_every_public_model_card_must_be_classified(self) -> None:
        changed = copy.deepcopy(self.registry)
        del changed["run_family_dispositions"]["opus"]
        errors = verifier.verify(changed)
        self.assertIn(
            "run-family registry does not cover every public model card",
            errors,
        )

    def test_evidence_hash_drift_fails(self) -> None:
        changed = copy.deepcopy(self.registry)
        changed["evidence_bindings"]["operant_cases.json"] = "0" * 64
        self.assertIn(
            "bound split evidence drift: operant_cases.json",
            verifier.verify(changed),
        )

    def test_required_evidence_binding_cannot_be_omitted(self) -> None:
        changed = copy.deepcopy(self.registry)
        del changed["evidence_bindings"]["operant_cases.json"]
        self.assertIn(
            "evidence binding coverage changed",
            verifier.verify(changed),
        )

    def test_private_overlay_digest_drift_fails(self) -> None:
        changed = copy.deepcopy(self.registry)
        changed["private_overlay_digests"][
            "gpt55-refusal-calibration-cases-v1.json"
        ] = "0" * 64
        self.assertIn(
            "private overlay digest registry drift",
            verifier.verify(changed),
        )

    def test_confirmatory_gate_cannot_drop_null_result_preservation(self) -> None:
        changed = copy.deepcopy(self.registry)
        changed["confirmatory_admission_requirements"].remove(
            "failed_null_excluded_and_interrupted_attempts_preserved"
        )
        self.assertIn(
            "confirmatory admission gate is incomplete",
            verifier.verify(changed),
        )

    def test_default_generator_seed_domains_are_disjoint(self) -> None:
        template_count = len(
            verifier._read_json(verifier.ROOT / "operant_templates.json")["templates"]
        )
        public_seeds = {
            gen_cases.SPLIT_BASE["public"] + index * 1000 + template
            for index in range(gen_cases.MAX_INSTANTIATIONS)
            for template in range(template_count)
        }
        private_seeds = {
            gen_cases.SPLIT_BASE["private"] + index * 1000 + template
            for index in range(gen_cases.MAX_INSTANTIATIONS)
            for template in range(template_count)
        }
        self.assertTrue(public_seeds.isdisjoint(private_seeds))

    def test_generator_rejects_unbounded_or_custom_seed_domains(self) -> None:
        with self.assertRaises(ValueError):
            gen_cases.generate(gen_cases.MAX_INSTANTIATIONS + 1, "public")
        with self.assertRaises(TypeError):
            gen_cases.generate(1, "public", 9000)

    def test_public_contract_requires_split_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            public = shutil.copytree(
                verifier.ROOT / "lab" / "public",
                Path(tmp) / "public",
            )
            (public / "evaluation-split-registry.json").unlink()
            self.assertIn(
                "missing required file: evaluation-split-registry.json",
                validate_public_artifacts(public),
            )


if __name__ == "__main__":
    unittest.main()
