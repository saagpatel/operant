#!/usr/bin/env python3
"""OPERANT self-serve OCS runner — point it at YOUR agent, get a protocol-bound score.

OPERANT measures an operator agent's *decision calibration*: does it withhold
(REFUSE / ESCALATE / REROUTE) on genuinely guarded or adversarial cases and proceed
on the matched benign twins — without over-refusing? The headline metric is OCS
(Operational Calibration Score) = TPR - FPR, which is bidirectional: a refuse-all or
proceed-all agent both score 0. Only calibrated discrimination scores above 0.

You bring the agent; this runs the published case corpus through it, scores the
captured answers deterministically, and emits a shareable report card + JSON + badge.
The receipt does not prove model identity or equivalence to any named model.

THREE WAYS TO PLUG IN YOUR AGENT (choose exactly one):

  # 1. Any CLI agent — prompt substituted into a command template ({prompt}) or via stdin
  python3 score_my_agent.py --cmd 'my-agent --quiet {prompt}' --label my-agent
  python3 score_my_agent.py --cmd 'my-agent --stdin' --cmd-stdin --label my-agent

  # 2. A Python callable — respond(prompt: str) -> str, as module:func or path.py:func
  python3 score_my_agent.py --adapter examples/heuristic_agent.py:respond --label heuristic

  # 3. An HTTP endpoint — prompt JSON-escaped into the body, answer pulled by dotted path
  python3 score_my_agent.py --endpoint https://my-agent/run \
      --http-body '{"input": "{prompt}"}' --answer-path output.text --label my-agent

Decision-axis OCS scores deterministically and free. The orchestration axis runs an
LLM judge by default (needs a judge model); pass --no-judge to skip it, or it degrades
loudly (the report says it was not scored) when no judge model is reachable.

Drop in a harder corpus with --cases '/path/to/operant*_cases.json' (e.g. an adversarial
expansion) — no code change required.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from operant_lab import selfserve
from operant_lab.agent_runners import make_runner

HERE = Path(__file__).resolve().parent


def _parse_headers(pairs: list[str] | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    for raw in pairs or []:
        if ":" not in raw:
            raise SystemExit(f"--http-header must be 'Name: value', got: {raw!r}")
        name, value = raw.split(":", 1)
        headers[name.strip()] = value.strip()
    return headers


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="score_my_agent.py",
        description="Score your agent's operating-decision calibration (OCS).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    src = ap.add_argument_group("agent source (choose exactly one)")
    src.add_argument("--cmd", help="CLI command template; use {prompt} or pair with --cmd-stdin")
    src.add_argument(
        "--cmd-stdin", action="store_true", help="pipe the prompt to the command's stdin"
    )
    src.add_argument("--adapter", help="Python entrypoint 'module:func' or 'path.py:func'")
    src.add_argument("--endpoint", help="HTTP endpoint URL")
    src.add_argument("--http-method", default="POST", help="HTTP method (default POST)")
    src.add_argument(
        "--http-header", action="append", metavar="'Name: value'", help="repeatable HTTP header"
    )
    src.add_argument(
        "--http-body",
        default='{"prompt": "{prompt}"}',
        help='request body template with {prompt} (default: {"prompt": "{prompt}"})',
    )
    src.add_argument(
        "--answer-path", help="dotted path to the answer in the JSON response, e.g. output.text"
    )

    run = ap.add_argument_group("run options")
    run.add_argument("--label", required=True, help="name for your agent in the report")
    run.add_argument(
        "--axes",
        choices=["all", "decision"],
        default="all",
        help="'all' (decision + orchestration) or 'decision' only (default all)",
    )
    run.add_argument("--no-judge", action="store_true", help="skip the orchestration LLM judge")
    run.add_argument("--judge-model", default=None, help="judge model for the orchestration axis")
    run.add_argument(
        "--cases",
        default=None,
        help="glob overriding the DECISION corpus (axes 1/2/4; e.g. the 4b corpus)",
    )
    run.add_argument(
        "--orch-cases", default=None, help="path/glob overriding the ORCHESTRATION corpus (axis 3)"
    )
    run.add_argument(
        "--operator-contract", default=None, help="path to the operator contract (system prompt)"
    )
    run.add_argument("--concurrency", type=int, default=4, help="parallel dispatches (default 4)")
    run.add_argument("--timeout", type=int, default=900, help="per-case dispatch timeout seconds")
    run.add_argument("--out", default=str(HERE / "results" / "self-serve"), help="output directory")
    run.add_argument(
        "--dry-run", action="store_true", help="print the plan and one sample prompt, no dispatch"
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        runner = make_runner(
            cmd=args.cmd,
            cmd_stdin=args.cmd_stdin,
            adapter=args.adapter,
            endpoint=args.endpoint,
            answer_path=args.answer_path,
            http_method=args.http_method,
            http_headers=_parse_headers(args.http_header),
            http_body=args.http_body,
            timeout=args.timeout,
        )
    except ValueError as exc:
        raise SystemExit(f"agent source error: {exc}")

    contract, contract_source = selfserve.resolve_operator_contract(args.operator_contract)
    prompts = selfserve.build_system_prompts(contract)
    axes = selfserve.load_axes(args.cases, args.orch_cases)
    decision_cases = axes["decision"]
    orch_cases = axes["orchestration"] if args.axes == "all" else {}
    judge_enabled = args.axes == "all" and not args.no_judge

    print(
        f"OPERANT self-serve — agent={runner.descriptor!r} shell={runner.shell}\n"
        f"  contract={contract_source}  corpus={args.cases or 'canonical'}\n"
        f"  decision_cases={len(decision_cases)}  orchestration_cases={len(orch_cases)}  "
        f"judge={'on' if judge_enabled else 'off'}  concurrency={args.concurrency}",
        flush=True,
    )

    if args.dry_run:
        sample_cid, sample_case = next(iter(decision_cases.items()))
        from operant_lab.agent_runners import build_byo_prompt

        print(f"\n*** DRY RUN — no dispatch ***\nSample prompt for case `{sample_cid}`:\n")
        print(build_byo_prompt(sample_case, prompts["decision"])[:1200] + "\n...[truncated]")
        return 0

    # on_progress fires from worker threads; serialize prints so concurrent
    # progress lines don't interleave.
    import threading

    print_lock = threading.Lock()

    def progress(cid: str, res) -> None:
        mark = "ok" if res.ok else f"FAIL({res.error})"
        with print_lock:
            print(f"  [{cid}] {res.duration_s}s -> {mark}", flush=True)

    print("\n[decision axes 1/2/4]")
    d_answers = selfserve.dispatch_cases(
        runner,
        decision_cases,
        prompts["decision"],
        concurrency=args.concurrency,
        on_progress=progress,
    )
    decision = selfserve.score_decision(decision_cases, d_answers)

    if orch_cases:
        print("\n[orchestration axis 3]")
        o_answers = selfserve.dispatch_cases(
            runner,
            orch_cases,
            prompts["orchestration"],
            concurrency=args.concurrency,
            on_progress=progress,
        )
        if judge_enabled:
            print("  judging orchestration plans (LLM judge)…", flush=True)
        orchestration = selfserve.score_orchestration(
            orch_cases, o_answers, judge_model=args.judge_model, enabled=judge_enabled
        )
    else:
        orchestration = {
            "status": "skipped",
            "reason": "decision-only run (--axes decision)",
            "n": 0,
        }

    summary = selfserve.build_summary(
        label=args.label,
        runner=runner,
        contract_source=contract_source,
        decision=decision,
        orchestration=orchestration,
        n_decision_cases=len(decision_cases),
        n_orch_cases=len(orch_cases),
        cases_glob=args.cases,
    )
    paths = selfserve.write_outputs(Path(args.out), summary)

    print("\n" + "=" * 64)
    print(selfserve.render_badge_text(summary))
    print(summary["band_detail"])
    print("=" * 64)
    rl = decision.get("rate_limited") or []
    err = decision.get("errored") or []
    if rl or err:
        print(
            f"!! {len(rl)} rate-limited + {len(err)} errored decision dispatches — scores are INCOMPLETE."
        )
    print("\nWrote:")
    for name, p in paths.items():
        print(f"  {name}: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
