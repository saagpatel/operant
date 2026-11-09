"""Deterministic scoring against the manifest-bound decision corpus."""
import json

import score_operant
from operant_lab.agent_runners import build_byo_prompt
from operant_lab.selfserve import classify_band
from run_operant import build_system_prompt
from sittings import manifest


def load_corpus():
    cases = {}
    for path in manifest.corpus_paths():
        if path == manifest.OPERATOR_CONTRACT:
            continue
        data = json.loads((manifest.REPO_ROOT / path).read_text(encoding="utf-8"))
        for pair in data.get("pairs", []):
            for side in ("malign", "benign"):
                case = dict(pair[side])
                case.update(pair_id=pair["pair_id"], tier=pair["tier"],
                            axis=pair.get("axis", data.get("axis", "refusal-calibration")), side=side)
                if case["id"] in cases:
                    raise ValueError("duplicate case id")
                cases[case["id"]] = case
    return cases


def system_prompt():
    return build_system_prompt((manifest.REPO_ROOT / manifest.OPERATOR_CONTRACT).read_text(encoding="utf-8"))


def prompt_for(case):
    return build_byo_prompt(case, system_prompt())


def score_answers(case_map, answers):
    seen = set()
    rows = []
    for answer in answers:
        cid = answer["case_id"]
        if cid not in case_map or cid in seen:
            raise ValueError("unknown or duplicate case")
        seen.add(cid)
        rows.append(score_operant.score_one(case_map[cid], answer["answer"]))
    agg = score_operant.aggregate(rows)
    return {"ocs": agg["ocs"], "tpr": agg["tpr"], "fpr": agg["fpr"],
            "n_scored": agg["n"] - agg["unparseable"], "n_cases": len(case_map),
            "n_unanswered": len(case_map) - len(seen), "band": classify_band(agg["ocs"])["band"]}
