#!/usr/bin/env python3
"""Fail-closed verification for OPERANT adaptive/confirmatory separation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REGISTRY = ROOT / "lab" / "public" / "evaluation-split-registry.json"

EXPECTED_BOUNDARY = (
    "This registry classifies evaluation role and contamination risk. It does "
    "not prove score correctness, receipt authenticity, served-model identity, "
    "or independent replication."
)
EXPECTED_WORDING = (
    "No existing OPERANT score is confirmatory under the checked registry."
)
EXPECTED_BINDING_PATHS = {
    "docs/evaluation-split-policy.md",
    "docs/gpt55-codex-error-analysis.md",
    "docs/gpt55-refusal-calibration-followup-plan.md",
    "docs/gpt55-sanctioned-path-followup-plan.md",
    "gen_cases.py",
    "generated/operant_public_cases.json",
    "lab/followup/gpt55-refusal-calibration-slice-v1.json",
    "lab/followup/gpt55-sanctioned-path-slice-v1.json",
    "lab/public/benchmark-card.json",
    "operant_axis2_cases.json",
    "operant_axis3_cases.json",
    "operant_axis4_cases.json",
    "operant_cases.json",
    "operant_templates.json",
}
EXPECTED_SPLITS = {
    "canonical-current-suite": (
        "OPEN_DEVELOPMENT_BENCHMARK",
        "PUBLIC",
        "UNKNOWN",
        "INCOMPLETE",
    ),
    "generated-public": (
        "OPEN_DEVELOPMENT_SURFACE_SET",
        "PUBLIC",
        "NOT_INDEPENDENT_SHARED_TEMPLATES",
        "OPEN_FOR_ITERATION",
    ),
    "generated-private": (
        "SURFACE_HOLDOUT_ONLY",
        "PUBLICLY_DERIVABLE_SURFACE_HOLDOUT",
        "NOT_INDEPENDENT_SHARED_TEMPLATES",
        "NOT_PROSPECTIVELY_SEALED",
    ),
    "gpt55-model-specific-followups": (
        "ADAPTIVE_DIAGNOSTIC",
        "PRIVATE_PROMPTS_PUBLIC_SUMMARIES",
        "NOT_INDEPENDENT_SELECTED_FROM_OBSERVED_ERRORS",
        "DOCUMENTED_ADAPTIVE",
    ),
    "prospective-confirmatory": (
        "RESERVED_NOT_ESTABLISHED",
        "NO_REGISTERED_SET",
        "UNKNOWN",
        "NOT_APPLICABLE",
    ),
}
EXPECTED_RUN_ROLES = {
    "cc-fable-interactive-batch": "ORPHANED_NONCONFIRMATORY",
    "codex-cli-gpt55-decision-gap": "ADAPTIVE_COVERAGE_DIAGNOSTIC",
    "codex-gpt55-decision": "HISTORICAL_ROLE_UNKNOWN",
    "codex-gpt55-exact-smoke": "SMOKE_ONLY",
    "codex-gpt55-local-authority-followup": "ADAPTIVE_DIAGNOSTIC",
    "codex-gpt55-refusal-calibration-followup": "ADAPTIVE_DIAGNOSTIC",
    "codex-gpt55-sanctioned-path-followup": "ADAPTIVE_DIAGNOSTIC",
    "haiku": "HISTORICAL_ROLE_UNKNOWN",
    "opus": "HISTORICAL_ROLE_UNKNOWN",
    "sonnet": "HISTORICAL_ROLE_UNKNOWN",
}
EXPECTED_REQUIREMENTS = {
    "timestamped_preregistration_before_subject_outputs",
    "immutable_case_contract_scorer_judge_and_environment_hashes",
    "structural_independence_from_development_and_error_analysis",
    "case_author_exposure_and_prior_use_record",
    "sealed_set_and_auditable_unblinding_event",
    "independently_verifiable_subject_identity_and_treatment_receipt",
    "predefined_exclusions_stopping_rule_and_analysis_plan",
    "failed_null_excluded_and_interrupted_attempts_preserved",
    "deviations_recorded_before_result_interpretation",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_path(root: Path, value: object) -> Path | None:
    if not isinstance(value, str):
        return None
    relative = Path(value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        return None
    return root / relative


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _public_run_families(public_dir: Path, errors: list[str]) -> set[str]:
    families: set[str] = set()
    try:
        calibration = _read_json(
            public_dir / "calibration-profiles.json"
        )
        models = calibration.get("models", [])
        families.update(
            str(row["run_family"])
            for row in models
            if isinstance(row, dict) and isinstance(row.get("run_family"), str)
        )
    except (OSError, json.JSONDecodeError, AttributeError):
        errors.append("public calibration profiles are unreadable")
    cards_dir = public_dir / "model-cards"
    if not cards_dir.is_dir():
        errors.append("public model-card directory is unavailable")
    else:
        families.update(path.stem for path in cards_dir.glob("*.json"))
    return families


def verify(
    data: dict[str, Any],
    *,
    root: Path = ROOT,
    public_dir: Path | None = None,
) -> list[str]:
    errors: list[str] = []
    public_dir = public_dir or root / "lab" / "public"
    if data.get("schema") != "operant-evaluation-split-registry.v1":
        errors.append("unsupported evaluation split registry schema")
    if data.get("as_of") != "2026-07-17":
        errors.append("evaluation split registry as_of changed")
    if data.get("claim_boundary") != EXPECTED_BOUNDARY:
        errors.append("evaluation split claim boundary changed")
    if data.get("current_confirmatory_status") != "NOT_ESTABLISHED":
        errors.append("confirmatory status must remain NOT_ESTABLISHED")
    if data.get("confirmatory_claim_allowed") is not False:
        errors.append("confirmatory claims must remain prohibited")
    if data.get("allowed_public_wording") != EXPECTED_WORDING:
        errors.append("allowed confirmatory wording changed")

    bindings = data.get("evidence_bindings")
    if not isinstance(bindings, dict) or not bindings:
        errors.append("evidence bindings are missing")
        bindings = {}
    if set(bindings) != EXPECTED_BINDING_PATHS:
        errors.append("evidence binding coverage changed")
    for relative, expected_digest in bindings.items():
        path = _safe_path(root, relative)
        if (
            path is None
            or not path.is_file()
            or not isinstance(expected_digest, str)
            or not SHA256_RE.fullmatch(expected_digest)
            or _sha256(path) != expected_digest
        ):
            errors.append(f"bound split evidence drift: {relative}")

    try:
        benchmark = _read_json(public_dir / "benchmark-card.json")
        public_private_digests = benchmark.get("evidence_binding", {}).get(
            "private_case_overlays"
        )
    except (OSError, json.JSONDecodeError, AttributeError):
        public_private_digests = None
        errors.append("public benchmark evidence binding is unreadable")
    if data.get("private_overlay_digests") != public_private_digests:
        errors.append("private overlay digest registry drift")

    dispositions = data.get("split_dispositions")
    if not isinstance(dispositions, list):
        errors.append("split dispositions are missing")
        dispositions = []
    by_id = {
        row.get("split_id"): row for row in dispositions if isinstance(row, dict)
    }
    if len(by_id) != len(dispositions) or set(by_id) != set(EXPECTED_SPLITS):
        errors.append("split disposition coverage changed")
    for split_id, expected in EXPECTED_SPLITS.items():
        row = by_id.get(split_id)
        if not isinstance(row, dict):
            continue
        observed = (
            row.get("evaluation_role"),
            row.get("exposure_state"),
            row.get("structural_independence"),
            row.get("adaptation_history"),
        )
        if observed != expected:
            errors.append(f"{split_id}: split classification changed")
        if row.get("confirmatory_eligible") is not False:
            errors.append(f"{split_id}: unavailable confirmatory evidence upgraded")
        if not isinstance(row.get("reason"), str) or not row["reason"].strip():
            errors.append(f"{split_id}: missing disposition reason")

    roles = data.get("run_family_dispositions")
    if not isinstance(roles, dict):
        errors.append("run-family evaluation roles changed")
        roles = {}
    observed_families = _public_run_families(public_dir, errors)
    if any(roles.get(family) != role for family, role in EXPECTED_RUN_ROLES.items()):
        errors.append("run-family evaluation roles changed")
    extra_roles = set(roles) - set(EXPECTED_RUN_ROLES)
    if any(
        roles.get(family) != "UNREGISTERED_EXPERIMENTAL_NONCONFIRMATORY"
        for family in extra_roles
    ):
        errors.append("unregistered run family received an unsafe evaluation role")
    if not observed_families.issubset(set(roles)):
        errors.append("run-family registry does not cover every public model card")

    requirements = data.get("confirmatory_admission_requirements")
    if (
        not isinstance(requirements, list)
        or len(requirements) != len(set(requirements))
        or set(requirements) != EXPECTED_REQUIREMENTS
    ):
        errors.append("confirmatory admission gate is incomplete")

    readme = (root / "README.md").read_text(encoding="utf-8")
    generator = (root / "gen_cases.py").read_text(encoding="utf-8")
    methodology = (public_dir / "methodology.md").read_text(encoding="utf-8")
    public_readme = (public_dir / "README.md").read_text(encoding="utf-8")
    if re.search(r"contamination[- ]proof", readme, flags=re.IGNORECASE):
        errors.append("README still claims contamination proofing")
    if "contamination-proofing primitive" in generator.lower():
        errors.append("generator still claims contamination proofing")
    if (
        "surface holdout" not in readme.lower()
        or "surface holdout" not in generator.lower()
    ):
        errors.append("surface-holdout boundary is missing")
    normalized_methodology = re.sub(r"\s+", " ", methodology)
    if "NOT ESTABLISHED" not in normalized_methodology:
        errors.append("public methodology omits NOT ESTABLISHED status")
    if "no current profile is registered as confirmatory" not in public_readme.lower():
        errors.append("public scorecard omits nonconfirmatory boundary")
    return errors


def main() -> None:
    try:
        data = _read_json(REGISTRY)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"unreadable evaluation split registry: {exc}") from exc
    errors = verify(data)
    if errors:
        raise SystemExit("\n".join(errors))
    print("OK: OPERANT adaptive/confirmatory split contract")


if __name__ == "__main__":
    argparse.ArgumentParser(
        description="Verify the OPERANT adaptive/confirmatory split contract."
    ).parse_args()
    main()
