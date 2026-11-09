"""Prompt assembly for runner sessions; imports stay outside the replay scorer."""

from typing import Any

from operant_lab.agent_runners import build_byo_prompt
from run_operant import build_system_prompt
from sittings import manifest
from sittings.canonical import canonical_sha256, sha256_hex
from sittings.corpus import load_corpus


def system_prompt(contract: str | None = None) -> str:
    if contract is None:
        contract = (manifest.REPO_ROOT / manifest.OPERATOR_CONTRACT).read_text(encoding="utf-8")
    return build_system_prompt(contract)


def prompt_for(case: dict[str, Any], *, contract: str | None = None) -> str:
    return build_byo_prompt(case, system_prompt(contract))


def expected_prompts(corpus: dict, *, corpus_bytes=None, trusted_corpora=()) -> dict[str, str]:
    """Use live builder code with hash-checked bound data, never archived code.

    Additional release hashes must come from verifier policy, not receipt fields.
    """
    live = manifest.corpus_manifest()
    if corpus["sha256"] not in {live["sha256"], *trusted_corpora}:
        raise ValueError("corpus release not trusted; prompts not rechecked")
    files = corpus["files"]
    if canonical_sha256([
        {"path": item["path"], "sha256": item["sha256"]} for item in files
    ]) != corpus["sha256"]:
        raise ValueError("corpus aggregate mismatch")
    if corpus_bytes is None:
        # Only read known live paths; a receipt cannot select filesystem paths.
        corpus_bytes = {
            item["path"]: (manifest.REPO_ROOT / item["path"]).read_bytes()
            for item in live["files"]
        }
    if set(corpus_bytes) != {item["path"] for item in files} or any(
        sha256_hex(corpus_bytes[item["path"]]) != item["sha256"]
        or len(corpus_bytes[item["path"]]) != item["bytes"] for item in files
    ):
        raise ValueError("bound corpus bytes unavailable or mismatched")
    contract = corpus_bytes[manifest.OPERATOR_CONTRACT].decode("utf-8")
    case_bytes = {
        path: data for path, data in corpus_bytes.items()
        if path.endswith("_cases.json") and path != "operant_axis3_cases.json"
    }
    return {
        case_id: prompt_for(case, contract=contract)
        for case_id, case in load_corpus(corpus_bytes=case_bytes).items()
    }
