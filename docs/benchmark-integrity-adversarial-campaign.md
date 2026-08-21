# Benchmark Integrity Adversarial Campaign

This queue covers the existing OPERANT benchmark, self-serve runner, public lab,
and MCP package only. Inputs are synthetic and local. It does not establish real
model quality, provider availability, publication, deployment, or human review.

Status vocabulary: `READY`, `WORKING`, `BLOCKED`, `VERIFIED`, `DROPPED`.

## Queue

| ID | Status | Failure family | Required disposition / proof |
|---|---|---|---|
| BI-01 | VERIFIED | Test isolation and candidate contamination | Standard selftest is synthetic and checkout-local; shell/Python candidates execute in isolated process groups; `test_timeout_keeps_partial_diagnostics_and_kills_descendants` proves descendant cleanup. |
| BI-02 | VERIFIED | Nonzero exits and claimed success | Shell exits retain only byte counts/digests and cannot become answers; existing incomplete-attempt selftest proves nonzero dispatch removes stale projections and exits nonzero. |
| BI-03 | VERIFIED | Signals and cancellation | `test_signal_termination_is_distinct_and_never_an_answer` distinguishes `signal_SIGTERM`; timeout and Python-adapter tests cover cancellation. |
| BI-04 | VERIFIED | Timeouts, hangs, partial output, and cleanup | Deterministic timeout fixture preserves digest-only partial diagnostics, terminates the process group, and proves its child cannot write after cancellation. |
| BI-05 | VERIFIED | Truncated, oversized, malformed, and missing output/artifacts | Shell and HTTP byte ceilings fail closed; interrupted multi-file publication cleans every exact projection; stale artifacts are invalidated before dispatch. |
| BI-06 | VERIFIED | Deterministic seeds, ordering, clock, and environment | Seeded bootstrap/generator selftests pass; dispatch returns corpus order; input binding is order-independent and excludes volatile clock/environment state; runner elapsed diagnostics use monotonic time, proven by `test_runner_duration_ignores_wall_clock_adjustments`; ClockBomb V1 scanned 91 files and 94 visible commits with no findings or unknown coverage. |
| BI-07 | VERIFIED | Flaky retries, concurrency, duplicates, and idempotent replay | Tests prove one dispatch per case, duplicate case IDs are rejected, distinct labels cannot collide, and a directory-level lock serializes singleton badges. |
| BI-08 | VERIFIED | Scoring boundaries and special numbers | Empty/missing-class cohorts are `UNDEFINED_*`; NaN/infinity/out-of-range OCS cannot classify or serialize; exact seeded statistical boundary tests remain green. |
| BI-09 | VERIFIED | Selection bias, leakage, and metric gaming | Partial variance repeats are rejected wholesale; duplicate IDs, incomplete cohorts, bypass gates, evaluation-split roles, and public/private claim boundaries fail closed. |
| BI-10 | VERIFIED | Process, artifact, evaluator, consumer, and human status | Process failure blocks scoring; evaluator failure blocks publication; MCP domain failures set `isError`; local receipts explicitly disclaim identity, deployment, certification, and human proof. |
| BI-11 | VERIFIED | Provenance and immutable input/config identity | Self-serve summaries bind contract, corpora, runner descriptor, and protocol bytes; stale/mismatched bindings are rejected; the split registry digest was refreshed without changing its role boundary. |
| BI-12 | VERIFIED | Harness ablation, bypass, evaluator crash, resume, and interrupted writes | Existing harness-ablation, lineage, manifest, failure-eval, and public-generation tests plus new evaluator-crash/atomic-write tests cover fail-closed interruption and recovery. |
| BI-13 | VERIFIED | CLI, API, and MCP error contracts | Positive CLI integers, explicit empty-corpus/variance exits, JSON readback, 42 MCP tests, typecheck, stdio probe, manifest signature, and version parity pass. |
| BI-14 | VERIFIED | Resource ceilings and bounded synthetic workloads | Shell, HTTP, and Python adapter execution is time/byte bounded; concurrency is positive and finite; every new workload is synthetic and local. |
| BI-15 | VERIFIED | Full local gate and saturation | Python 3.12: 150 tests with `ResourceWarning` promoted to error, both selftests, split verifier, compileall, Ruff 0.15.22; MCP: 42 tests and every declared local gate; three saturation lenses reran clean. |

## Baseline

- Binding: live remote default `main` at `ad4539b01504ce7e891bf8dd23e658a32189ad0c`.
- Python 3.12 unittest discovery: 127 tests passed.
- Evaluation-split verification: passed.
- Standard selftest: failed because it read machine-local sibling results while
  its expected private overlay directory was absent in the clean task worktree;
  three checks failed and the test crashed with `StopIteration`.
- Ruff 0.15.12 initially failed with five findings; the isolated exact CI pin
  Ruff 0.15.22 now passes after behavior-neutral cleanup.
- MCP baseline and final gates ran in a task-isolated HOME/cache. Final result:
  42 tests plus corpus/well-known generation readback, typecheck, CLI build,
  stdio probe, manifest signature verification, and version parity all pass.

## Saturation passes

1. Failure-path/static search: searched scoped Python and MCP sources for silent
   exception swallowing, unclosed JSON reads, process/timeout handling, artifact
   replacement, special-number serialization, and domain errors. It found and
   repaired partial-repeat selection bias and an unclosed generator input; the
   independent rerun found no new material safe item.
2. Warning and timing sensitivity: promoted `ResourceWarning` to an error across
   full unittest discovery and the standard selftest. Review rerun found the
   timeout proof's remaining 300 ms startup margin was still unstable under full
   load; the bounded synthetic child was widened to a 2 s timeout with no weaker
   assertion. A ClockBomb V1 pass then found no temporal literals, ambient-clock
   hazards in deterministic paths, or temporal-settlement churn; a targeted
   wall-clock adjustment fixture did expose runner duration diagnostics using
   `time.time()`, which was repaired with `time.monotonic()`. Five isolated
   repetitions and the parent rerun passed 150 tests with no new material safe
   item.
3. Consumer contract/readback: searched public wording and runnable commands,
   exercised the local CLI JSON readback, verified empty variance exits 2,
   reverified the evaluation-split contract, and ran all MCP public gates. Manual
   readback found and repaired custom-working-directory adapter isolation; the
   independent rerun found no new material safe item.

## Claim boundary

`VERIFIED` means only deterministic local proof on this task branch. External
provider, CI, installed package, deployed MCP, published result, real-model,
independent-custody, and human-acceptance proof remain separate and unperformed.
