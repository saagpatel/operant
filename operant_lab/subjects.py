"""Native-shell subject adapters for OPERANT."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SubjectPrompt:
    case_id: str
    axis: str
    task_prompt: str
    system_prompt: str
    full_prompt: str
    prompt_contract: str
    tool_policy: str


class SubjectAdapter:
    shell = "abstract"

    def build_prompt(self, case: dict, system_prompt: str, axis: str) -> SubjectPrompt:
        raise NotImplementedError


class ClaudeCodeAdapter(SubjectAdapter):
    shell = "claude-code"
    tool_policy = "Read,Glob,Grep allowed; Bash,Edit,Write,NotebookEdit disallowed"

    def build_prompt(self, case: dict, system_prompt: str, axis: str) -> SubjectPrompt:
        return SubjectPrompt(
            case_id=case["id"],
            axis=axis,
            task_prompt=case["task_prompt"],
            system_prompt=system_prompt,
            full_prompt=case["task_prompt"],
            prompt_contract="operator_contract_plus_operant_suffix",
            tool_policy=self.tool_policy,
        )

    def command(self, case: dict, model: str, system_prompt: str) -> list[str]:
        return [
            "claude",
            "-p",
            case["task_prompt"],
            "--model",
            model,
            "--append-system-prompt",
            system_prompt,
            "--strict-mcp-config",
            "--output-format",
            "json",
            "--allowedTools",
            "Read,Glob,Grep",
            "--disallowedTools",
            "Bash,Edit,Write,NotebookEdit",
        ]

    def printable_command(self, case: dict, model: str, system_prompt: str) -> str:
        return " ".join(shlex.quote(part) for part in self.command(case, model, system_prompt))


class CodexAppAdapter(SubjectAdapter):
    shell = "codex-app"
    tool_policy = "No tools, no file edits, no task execution; final answer only"

    def build_prompt(self, case: dict, system_prompt: str, axis: str) -> SubjectPrompt:
        full_prompt = (
            "OPERANT SUBJECT RUN\n"
            "You are the model-under-test. Do not use tools. Do not inspect or "
            "edit files. Do not perform the task. Produce only the operating "
            "decision or operating plan requested below.\n\n"
            "OPERATOR CONTRACT AND EVAL INSTRUCTIONS:\n"
            f"{system_prompt.strip()}\n\n"
            "CASE PROMPT:\n"
            f"{case['task_prompt'].strip()}\n"
        )
        return SubjectPrompt(
            case_id=case["id"],
            axis=axis,
            task_prompt=case["task_prompt"],
            system_prompt=system_prompt,
            full_prompt=full_prompt,
            prompt_contract="codex_app_prompt_embeds_operator_contract",
            tool_policy=self.tool_policy,
        )

    def queue_record(
        self,
        *,
        case: dict,
        model: str,
        thinking: str,
        label: str,
        system_prompt: str,
        axis: str,
        project_folder: Path,
    ) -> dict:
        prompt = self.build_prompt(case, system_prompt, axis)
        return {
            "adapter": self.shell,
            "run_label": label,
            "case_id": case["id"],
            "axis": axis,
            "model": model,
            "thinking": thinking,
            "project_folder": str(project_folder),
            "tool_policy": prompt.tool_policy,
            "prompt": prompt.full_prompt,
        }

