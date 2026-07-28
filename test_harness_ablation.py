from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from operant_lab import harness_ablation as ablation


class HarnessAblationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = ablation.load_cases()

    def test_registry_covers_requested_cue_classes(self) -> None:
        variants = {variant["id"]: variant for variant in ablation.VARIANTS}
        self.assertEqual(len(variants), 7)
        self.assertEqual(variants["metadata_opaque"]["metadata"], "opaque_subject_view")
        self.assertEqual(variants["order_degrouped"]["order"], "seeded_degrouped")
        self.assertEqual(variants["response_minimal"]["system"], "minimal")
        self.assertEqual(
            variants["marker_alias"]["case_marker"],
            "SCENARIO UNDER REVIEW:",
        )
        self.assertEqual(
            variants["lexical_decorrelated"]["lexical"],
            "semantic_alias_bank",
        )

    def test_transformations_preserve_ground_truth(self) -> None:
        variant = ablation.variant_by_id("lexical_decorrelated")
        transformed, replacements = ablation.transform_cases(self.cases, variant, 11)
        self.assertGreater(replacements, 0)
        for case_id, original in self.cases.items():
            ablation.assert_ground_truth_unchanged(original, transformed[case_id])
        self.assertEqual(ablation.load_scorer().validate_cases(transformed), [])

    def test_semantic_aliases_reduce_observed_heuristic_cues(self) -> None:
        subject_module = ablation._load_module(
            "examples/heuristic_agent.py",
            "test_harness_ablation_subject",
        )
        observed_terms = (
            tuple(subject_module.UNTRUSTED_ORIGIN)
            + tuple(subject_module.DANGER)
            + tuple(subject_module.GUARDED_SANCTIONED)
        )
        variant = ablation.variant_by_id("lexical_decorrelated")
        transformed, _ = ablation.transform_cases(self.cases, variant, 23)
        original_hits = sum(
            term in case["task_prompt"].lower()
            for case in self.cases.values()
            for term in observed_terms
        )
        transformed_hits = sum(
            term in case["task_prompt"].lower()
            for case in transformed.values()
            for term in observed_terms
        )
        self.assertGreater(original_hits, 0)
        self.assertLess(transformed_hits, original_hits)

    def test_degrouped_order_is_complete_and_nonadjacent(self) -> None:
        ordered = ablation.case_order(self.cases, mode="seeded_degrouped", seed=47)
        self.assertEqual(set(ordered), set(self.cases))
        self.assertEqual(len(ordered), len(self.cases))
        for left, right in zip(ordered, ordered[1:]):
            self.assertNotEqual(
                self.cases[left]["pair_id"],
                self.cases[right]["pair_id"],
            )

    def test_prompt_variants_do_not_expose_case_metadata(self) -> None:
        case = next(iter(self.cases.values()))
        prompt = ablation.build_subject_prompt(
            case,
            ablation.variant_by_id("metadata_opaque"),
        )
        self.assertNotIn(case["id"], prompt)
        self.assertNotIn(case["pair_id"], prompt)
        self.assertNotIn(case["axis"], prompt)
        self.assertNotIn(case["class"], prompt)

    def test_prompt_free_contract_rejects_raw_fields(self) -> None:
        ablation.assert_prompt_free({"prompt_sha256": "a" * 64})
        with self.assertRaisesRegex(ValueError, "prompt-free contract"):
            ablation.assert_prompt_free({"task_prompt": "hidden"})

    def test_metrics_count_failures_without_dropping_attempts(self) -> None:
        attempts = [
            {
                "pair_ref_sha256": "pair",
                "axis": "refusal-calibration",
                "ground_truth_class": "guard_warranted",
                "withheld": True,
                "decision_accuracy": True,
                "safe_and_correct": True,
                "parse_status": "ok",
                "dispatch_status": "ok",
                "bypass_used": False,
            },
            {
                "pair_ref_sha256": "pair",
                "axis": "refusal-calibration",
                "ground_truth_class": "benign_open",
                "withheld": None,
                "decision_accuracy": False,
                "safe_and_correct": False,
                "parse_status": "not_attempted",
                "dispatch_status": "failed",
                "bypass_used": False,
            },
        ]
        metrics = ablation.compute_metrics(attempts)
        self.assertEqual(metrics["n_cases"], 2)
        self.assertEqual(metrics["dispatch_failures"], 1)
        self.assertEqual(metrics["decision_accuracy"], 0.5)
        self.assertEqual(metrics["pair_consistency"], 0.0)

    def test_paired_cluster_bootstrap_is_seeded(self) -> None:
        baseline = []
        treatment = []
        for index in range(4):
            for side, guard in (("g", True), ("b", False)):
                common = {
                    "pair_ref_sha256": f"pair-{index}",
                    "axis": "test",
                    "ground_truth_class": "guard_warranted" if guard else "benign_open",
                    "parse_status": "ok",
                    "dispatch_status": "ok",
                    "bypass_used": False,
                    "safe_and_correct": True,
                }
                baseline.append(
                    {
                        **common,
                        "withheld": guard,
                        "decision_accuracy": True,
                    }
                )
                treatment.append(
                    {
                        **common,
                        "withheld": True,
                        "decision_accuracy": guard,
                    }
                )
        first = ablation.paired_cluster_bootstrap_delta(
            baseline,
            treatment,
            metric="ocs",
            seed=9,
            resamples=100,
        )
        second = ablation.paired_cluster_bootstrap_delta(
            baseline,
            treatment,
            metric="ocs",
            seed=9,
            resamples=100,
        )
        self.assertEqual(first, second)
        self.assertEqual(first["n_pairs"], 4)

    def test_preregistration_has_required_boundaries(self) -> None:
        preregistration = ablation.build_preregistration("2026-07-27T00:00:00Z")
        self.assertEqual(preregistration["sample_size"]["unique_cases"], 40)
        self.assertEqual(preregistration["sample_size"]["matched_pairs"], 20)
        self.assertEqual(preregistration["failure_handling"]["retries"], 0)
        self.assertIn(
            "STOP — confirmatory treatment not currently admissible.",
            preregistration["admissibility_decision_rule"]["otherwise"],
        )
        self.assertFalse(
            preregistration["identities"]["subject"]["network_or_paid_api"]
        )

    def test_preregistration_write_is_exclusive(self) -> None:
        preregistration = ablation.build_preregistration("2026-07-27T00:00:00Z")
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "prereg.json"
            digest, sidecar = ablation.write_preregistration(preregistration, out)
            self.assertEqual(digest, ablation.sha256_file(out))
            self.assertTrue(sidecar.is_file())
            self.assertEqual(json.loads(out.read_text()), preregistration)
            with self.assertRaises(FileExistsError):
                ablation.write_preregistration(preregistration, out)

    def test_attempt_matrix_verifier_rejects_missing_cell(self) -> None:
        preregistration = {
            "transformations": {
                "variants": [{"id": "baseline_public"}, {"id": "treatment"}],
            },
            "sample_size": {
                "unique_cases": 2,
                "matched_pairs": 1,
                "planned_attempts": 4,
                "seeds_and_orders": [7],
            },
        }
        attempts = [
            {
                "schema": "operant-harness-ablation-attempt.v1",
                "attempt_id": f"a-{index}",
                "variant_id": "baseline_public",
                "seed": 7,
                "order_index": index,
                "case_ref_sha256": f"case-{index}",
                "pair_ref_sha256": "pair",
            }
            for index in range(2)
        ]
        with self.assertRaisesRegex(ValueError, "incomplete"):
            ablation._verify_attempt_matrix(preregistration, attempts)

    def test_replay_verifier_rejects_fabricated_match_flag(self) -> None:
        attempts = [{"attempt_id": "a"}]
        analysis = {"schema": "analysis"}
        receipt = {
            "execution": {
                "deterministic_replay": {
                    "attempt_core_sha256": ablation.canonical_digest(attempts),
                    "replay_attempt_core_sha256": "0" * 64,
                    "analysis_core_sha256": ablation.canonical_digest(analysis),
                    "replay_analysis_core_sha256": ablation.canonical_digest(analysis),
                    "matched": True,
                }
            }
        }
        with self.assertRaisesRegex(ValueError, "do not match"):
            ablation._verify_replay_digests(receipt, attempts, analysis)

    def test_analysis_verifier_rejects_metric_drift(self) -> None:
        variant = {
            "id": "baseline_public",
            "system": "canonical",
            "case_marker": "CASE PROMPT:",
            "metadata": "canonical_internal",
            "order": "canonical",
            "lexical": "canonical",
        }
        preregistration = {
            "transformations": {"variants": [variant]},
            "sample_size": {
                "unique_cases": 2,
                "matched_pairs": 1,
                "planned_attempts": 2,
                "seeds_and_orders": [7],
            },
        }
        attempts = []
        for index, (case_class, withheld) in enumerate(
            (("guard_warranted", True), ("benign_open", False))
        ):
            attempts.append(
                {
                    "schema": "operant-harness-ablation-attempt.v1",
                    "attempt_id": f"a-{index}",
                    "variant_id": "baseline_public",
                    "seed": 7,
                    "order_index": index,
                    "case_ref_sha256": f"case-{index}",
                    "pair_ref_sha256": "pair",
                    "axis": "test",
                    "ground_truth_class": case_class,
                    "withheld": withheld,
                    "decision_accuracy": True,
                    "safe_and_correct": True,
                    "parse_status": "ok",
                    "dispatch_status": "ok",
                    "bypass_used": False,
                }
            )
        bad_metrics = ablation.compute_metrics(attempts)
        bad_metrics["decision_accuracy"] = 0.0
        analysis = {
            "unique_cases": 2,
            "matched_pairs": 1,
            "seeds": [7],
            "attempt_count": 2,
            "ablation_matrix": [
                {
                    "variant": variant,
                    "seed_runs": [{"seed": 7, "metrics": bad_metrics}],
                }
            ],
        }
        with self.assertRaisesRegex(ValueError, "metrics drifted"):
            ablation._verify_analysis_calculations(
                preregistration,
                attempts,
                analysis,
            )


if __name__ == "__main__":
    unittest.main()
