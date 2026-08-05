"""Bring-your-own-agent runners for the self-serve OPERANT OCS scorer.

The benchmark's scoring core (`score_operant.score_one`, `score_orchestration_judge.judge_one`)
is already model-agnostic: it reads the agent's *answer text* and never cares how that
text was produced. The only thing a self-serve user must supply is a way to turn a
prompt into their agent's answer. That mapping is an `AgentRunner`.

Three runners cover essentially any agent, with zero third-party dependencies (the
whole repo is standard-library only, so "anyone can run it" stays literally true):

  - ShellCommandRunner   — any CLI agent. The prompt is substituted into a command
                           template (`{prompt}` placeholder) or piped via stdin; the
                           agent's stdout is its answer. Works for `claude`, `codex`,
                           `aider`, a `curl` one-liner, or a custom wrapper script.
  - PythonEntrypointRunner — an in-process `respond(prompt) -> str` callable, given as
                           `module:func` or `path/to/file.py:func`. For Python agents.
  - HTTPEndpointRunner   — a hosted agent behind an HTTP endpoint. The prompt is
                           JSON-escaped into a request body template and the answer is
                           pulled from the JSON response via a dotted path.

Every runner returns a `RunnerResult` carrying the answer text plus enough metadata
(`ok`, `error`, `duration_s`) for the orchestrator to tell a real answer apart from a
rate-limit / crash / empty response — the same discipline `run_suite.py` applies to
native dispatch.
"""

from __future__ import annotations

import importlib
import importlib.util
import hashlib
import json
import shlex
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROMPT_TOKEN = "{prompt}"
DEFAULT_MAX_ANSWER_BYTES = 1024 * 1024


def build_byo_prompt(case: dict, system_prompt: str) -> str:
    """Assemble the single self-contained prompt sent to a bring-your-own agent.

    A generic agent may not expose a separate system channel, so the operator
    contract + eval instructions are embedded inline ahead of the case — mirroring
    `CodexAppAdapter.build_prompt`, the existing embed-everything native shell. The
    leading guardrail tells the subject it is the model-under-test and must produce
    only the decision/plan, not perform the task.
    """
    return (
        "OPERANT SUBJECT RUN\n"
        "You are the model-under-test. Do not use tools. Do not inspect or edit "
        "files. Do not perform the task. Produce only the operating decision or "
        "operating plan requested below.\n\n"
        "OPERATOR CONTRACT AND EVAL INSTRUCTIONS:\n"
        f"{system_prompt.strip()}\n\n"
        "CASE PROMPT:\n"
        f"{case['task_prompt'].strip()}\n"
    )


@dataclass
class RunnerResult:
    """One agent invocation. `text` is the raw answer the scorer parses."""

    text: str
    ok: bool
    error: str | None = None
    duration_s: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


class AgentRunner:
    """Base contract: turn a fully-built prompt into the agent's answer text.

    Subclasses set `shell` (recorded as `subject_shell` in run artifacts) and a
    `descriptor` (a redactable, human-readable description of how the agent is
    invoked — no secrets) and implement `respond`.
    """

    shell = "byo"
    descriptor = "abstract"

    def respond(self, prompt: str) -> RunnerResult:  # pragma: no cover - abstract
        raise NotImplementedError


class ShellCommandRunner(AgentRunner):
    """Run a CLI agent: the prompt is substituted into a `{prompt}` placeholder (each
    matching argv token) or piped via stdin, and the agent's stdout is its answer.

    Dispatch uses `subprocess.run(..., shell=False)`, so the prompt is passed as a
    real argv element and is NOT interpreted by a shell. Caveat: if YOUR template
    itself delegates to a shell — e.g. `bash -c 'agent {prompt}'` — then the prompt
    lands inside a shell-interpreted string and metacharacters in case text would be
    evaluated. Prefer passing the prompt as a positional arg instead
    (`bash -c 'agent "$1"' _ {prompt}`) or use `prompt_via='stdin'`.

    A non-zero exit is always a failed attempt, even when stdout resembles a usable
    answer. Failed-process output is represented only by SHA-256 digests and byte
    counts; raw stdout/stderr cannot flow into retained results or console/CI logs.
    Successful stdout is bounded before it is admitted as an answer.
    """

    shell = "byo-shell"

    def __init__(
        self,
        command: str,
        *,
        prompt_via: str = "placeholder",
        timeout: int = 900,
        cwd: str | None = None,
        max_answer_bytes: int = DEFAULT_MAX_ANSWER_BYTES,
    ) -> None:
        if prompt_via not in ("placeholder", "stdin"):
            raise ValueError("prompt_via must be 'placeholder' or 'stdin'")
        if prompt_via == "placeholder" and PROMPT_TOKEN not in command:
            raise ValueError(
                f"command has no {PROMPT_TOKEN} placeholder; "
                "use prompt_via='stdin' to pipe the prompt instead"
            )
        self.command = command
        self.prompt_via = prompt_via
        self.timeout = timeout
        self.cwd = cwd
        if type(max_answer_bytes) is not int or max_answer_bytes < 1:
            raise ValueError("max_answer_bytes must be a positive integer")
        self.max_answer_bytes = max_answer_bytes
        self.descriptor = f"shell:{command}"

    def _argv(self, prompt: str) -> list[str]:
        parts = shlex.split(self.command)
        if self.prompt_via == "placeholder":
            return [part.replace(PROMPT_TOKEN, prompt) for part in parts]
        return parts

    def respond(self, prompt: str) -> RunnerResult:
        argv = self._argv(prompt)
        stdin = prompt if self.prompt_via == "stdin" else None
        t0 = time.time()
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            try:
                proc = subprocess.run(
                    argv,
                    input=stdin,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    text=True,
                    timeout=self.timeout,
                    cwd=self.cwd,
                )
            except subprocess.TimeoutExpired:
                return RunnerResult("", False, "timeout", round(time.time() - t0, 2))
            except (FileNotFoundError, OSError) as exc:
                return RunnerResult(
                    "",
                    False,
                    f"spawn_failed:{type(exc).__name__}",
                    round(time.time() - t0, 2),
                )

            def output_metadata(handle) -> tuple[str, int]:  # noqa: ANN001
                size = handle.tell()
                handle.seek(0)
                digest = hashlib.sha256()
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                return f"sha256:{digest.hexdigest()}", size

            stdout_digest, stdout_bytes = output_metadata(stdout_file)
            stderr_digest, stderr_bytes = output_metadata(stderr_file)
            meta = {
                "exit_code": proc.returncode,
                "stdout_sha256": stdout_digest,
                "stdout_bytes": stdout_bytes,
                "stderr_sha256": stderr_digest,
                "stderr_bytes": stderr_bytes,
            }
            dur = round(time.time() - t0, 2)
            if proc.returncode != 0:
                return RunnerResult("", False, f"exit_{proc.returncode}", dur, meta)
            if stdout_bytes > self.max_answer_bytes:
                return RunnerResult("", False, "stdout_too_large", dur, meta)
            stdout_file.seek(0)
            text = stdout_file.read(self.max_answer_bytes).decode("utf-8", errors="replace").strip()
            return RunnerResult(text, bool(text), None if text else "empty_stdout", dur, meta)


class PythonEntrypointRunner(AgentRunner):
    shell = "byo-python"

    # No timeout: an in-process callable cannot be safely interrupted in Python, so
    # we do not pretend to bound it. A hanging agent function blocks its dispatch
    # thread — wrap slow agents in a ShellCommandRunner/HTTPEndpointRunner (which do
    # enforce timeouts) if you need a hard limit.
    def __init__(self, spec: str) -> None:
        self.spec = spec
        self.descriptor = f"python:{spec}"
        self._func = self._resolve(spec)

    @staticmethod
    def _resolve(spec: str) -> Callable[[str], str]:
        if ":" not in spec:
            raise ValueError("adapter spec must be 'module:func' or 'path.py:func'")
        target, func_name = spec.rsplit(":", 1)
        if target.endswith(".py") or "/" in target:
            path = Path(target).resolve()
            mod_spec = importlib.util.spec_from_file_location(path.stem, path)
            if mod_spec is None or mod_spec.loader is None:
                raise ImportError(f"cannot load module from {path}")
            module = importlib.util.module_from_spec(mod_spec)
            mod_spec.loader.exec_module(module)
        else:
            module = importlib.import_module(target)
        func = getattr(module, func_name, None)
        if not callable(func):
            raise AttributeError(f"{spec} is not a callable")
        return func

    def respond(self, prompt: str) -> RunnerResult:
        t0 = time.time()
        try:
            text = self._func(prompt)
        except Exception as exc:  # noqa: BLE001 - surface any agent error as a failed run
            return RunnerResult("", False, f"raised: {exc!r}", round(time.time() - t0, 2))
        dur = round(time.time() - t0, 2)
        if not isinstance(text, str):
            return RunnerResult("", False, f"returned {type(text).__name__}, expected str", dur)
        text = text.strip()
        return RunnerResult(text, bool(text), None if text else "empty_return", dur)


def resolve_answer_path(payload: Any, dotted: str) -> Any:
    """Walk a dotted path into a decoded JSON payload. Integer segments index lists.

    e.g. 'choices.0.message.content' -> payload['choices'][0]['message']['content'].
    Returns None if any segment is missing rather than raising.
    """
    cur = payload
    if not dotted:
        return cur
    for seg in dotted.split("."):
        if isinstance(cur, list):
            try:
                idx = int(seg)
            except ValueError:
                return None
            if not (-len(cur) <= idx < len(cur)):
                return None
            cur = cur[idx]
        elif isinstance(cur, dict):
            if seg not in cur:
                return None
            cur = cur[seg]
        else:
            return None
    return cur


# Response keys an unconfigured endpoint most commonly returns the answer under.
DEFAULT_ANSWER_PATHS = ("answer", "text", "output", "response", "content", "result", "completion")


class HTTPEndpointRunner(AgentRunner):
    shell = "byo-http"

    def __init__(
        self,
        url: str,
        *,
        method: str = "POST",
        headers: dict[str, str] | None = None,
        body_template: str = '{"prompt": "{prompt}"}',
        answer_path: str | None = None,
        timeout: int = 300,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        if PROMPT_TOKEN not in body_template:
            raise ValueError(f"body template has no {PROMPT_TOKEN} placeholder")
        self.url = url
        self.method = method.upper()
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.body_template = body_template
        self.answer_path = answer_path
        self.timeout = timeout
        self._opener = opener
        self.descriptor = f"http:{self.method} {url}"

    def _body(self, prompt: str) -> bytes:
        # The prompt is always inserted as a JSON string value: json.dumps(prompt)
        # produces a fully-escaped, double-quoted literal (newlines, quotes,
        # backslashes handled). We substitute the same encoded literal whether the
        # template writes the token quoted (`"{prompt}"`) or bare (`{prompt}`), so
        # the body is valid JSON either way — `{"x": "{prompt}"}` and `{"x": {prompt}}`
        # both yield `{"x": "<escaped prompt>"}`. Put the token where a string value
        # goes; non-string prompt slots are not supported (prompts are text).
        encoded = json.dumps(prompt)  # includes surrounding double quotes
        body = self.body_template.replace(f'"{PROMPT_TOKEN}"', encoded).replace(
            PROMPT_TOKEN, encoded
        )
        return body.encode("utf-8")

    def _extract(self, raw: str) -> tuple[str, str | None]:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            # Endpoint returned plain text — accept it as the answer.
            return raw.strip(), None
        if self.answer_path:
            value = resolve_answer_path(payload, self.answer_path)
            if isinstance(value, str):
                return value.strip(), None
            return "", f"answer_path '{self.answer_path}' missing or non-string"
        if isinstance(payload, str):
            return payload.strip(), None
        for key in DEFAULT_ANSWER_PATHS:
            if isinstance(payload, dict) and isinstance(payload.get(key), str):
                return payload[key].strip(), None
        return "", f"no answer found; set --answer-path (tried {DEFAULT_ANSWER_PATHS})"

    def respond(self, prompt: str) -> RunnerResult:
        req = urllib.request.Request(
            self.url, data=self._body(prompt), headers=self.headers, method=self.method
        )
        t0 = time.time()
        try:
            with self._opener(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return RunnerResult(
                "", False, f"http_{exc.code}: {exc.reason}", round(time.time() - t0, 2)
            )
        except (urllib.error.URLError, OSError) as exc:
            return RunnerResult("", False, f"request_failed: {exc}", round(time.time() - t0, 2))
        dur = round(time.time() - t0, 2)
        text, err = self._extract(raw)
        return RunnerResult(text, bool(text and not err), err, dur)


def make_runner(
    *,
    cmd: str | None = None,
    cmd_stdin: bool = False,
    adapter: str | None = None,
    endpoint: str | None = None,
    answer_path: str | None = None,
    http_method: str = "POST",
    http_headers: dict[str, str] | None = None,
    http_body: str | None = None,
    timeout: int = 900,
) -> AgentRunner:
    """Construct exactly one runner from mutually-exclusive CLI-style options."""
    chosen = [
        name for name, val in (("cmd", cmd), ("adapter", adapter), ("endpoint", endpoint)) if val
    ]
    if len(chosen) != 1:
        raise ValueError(
            f"choose exactly one of --cmd / --adapter / --endpoint (got: {chosen or 'none'})"
        )
    if cmd:
        return ShellCommandRunner(
            cmd, prompt_via="stdin" if cmd_stdin else "placeholder", timeout=timeout
        )
    if adapter:
        return PythonEntrypointRunner(adapter)
    return HTTPEndpointRunner(
        endpoint,  # type: ignore[arg-type]
        method=http_method,
        headers=http_headers,
        body_template=http_body or '{"prompt": "{prompt}"}',
        answer_path=answer_path,
        timeout=min(timeout, 300),
    )
