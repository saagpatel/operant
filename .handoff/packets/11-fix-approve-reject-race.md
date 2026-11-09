# 11 — Fix approve/reject race in the review queue

Review finding (major), `sittings/review/queue.py` ~272: approval publishes a stale queue snapshot. If
`reject()` completes while receipt verification is running, approval still publishes the sitting and
`_move(..., "approved")` moves it out of `rejected`, silently discarding the rejection.

Fix: serialize approve, reject and unpublish per sitting with a shared inter-process lock (fcntl.flock on
a per-sitting lock file under the queue root; keep the existing web-data publication lock and take locks
in one fixed order to avoid deadlock). Re-read and validate the queue state under the lock immediately
before publication and refuse unless still `pending` (or already `approved`, for idempotent re-approve).
Regression test: pause verification (stub verifier that waits on an Event), complete `reject` from another
thread (it must either block until approval finishes or win and make approval refuse; assert the end state
is consistent: never "rejected but published", never "approved" after a rejection that completed first).

Edit only `sittings/review/` and `test_sittings_review.py`. No sockets/network in sandbox; uv cache writable.

## Acceptance
```sh
uv run python -m unittest test_sittings_review -v
uv run ruff check sittings ./*.py
```
