# 99 — Stress test (read-only analysis)

Read the project brief (the pick) and `.handoff/packets/00-contract.md` through `05-*.md`. Check the pick's risks against the real repo: the scorer (`score_operant.py`, `operant_lab/selfserve.py`, `operant_lab/agent_runners.py`, `run_operant.py`), the corpus files, `score_my_agent.py`, and CheckSeal (installed source, read-only).

Answer, with file:line evidence: (1) is the decision scoring fully deterministic and reproducible from the bound files only (any env vars, globals, time, randomness, home-dir reads, or files outside `sittings/manifest.py`'s lists that change a score?); (2) are there files the scorer imports or reads that are missing from `SCORER_FILES` or the corpus manifest; (3) attack surface of a hosted runner per the contract API and how a submitter could inflate a score (e.g. the corpus is public, so answers can be precomputed — what does a seal honestly bind then?); (4) anything in the contract that contradicts the code or CheckSeal's DSSE/keyid formats; (5) scope cuts you recommend.

Write the findings to `.handoff/stress-test.md` only. Change no other file. Commit plan: that one file, message `docs(sittings): add open sittings stress test`.

## Acceptance
```sh
test -s .handoff/stress-test.md
```
