"""Prompt assembly for runner sessions; imports stay outside the replay scorer."""

from typing import Any

from operant_lab.agent_runners import build_byo_prompt
from run_operant import build_system_prompt
from sittings import manifest


def system_prompt() -> str:
    contract = (manifest.REPO_ROOT / manifest.OPERATOR_CONTRACT).read_text(encoding="utf-8")
    return build_system_prompt(contract)


def prompt_for(case: dict[str, Any]) -> str:
    return build_byo_prompt(case, system_prompt())
