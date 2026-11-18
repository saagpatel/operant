"""Self-serve: non-answers land in the bucket the report card says they do.

The self-serve report card titles the `rate_limited` list "Rate-limited /
empty dispatches", and operant_lab/selfserve.py defines RATE_LIMIT_MARKERS =
("session limit", "usage limit", "rate limit"). RUN-PLAN.md section 4: "A
throttled dispatch returns a report containing "session limit" / "usage limit"
or an empty body."

score_decision used to file a non-answer under rate_limited only when the
bare substring "limit" appeared in its text, so an empty dispatch landed in
`errored` and a crash whose message mentioned any limit landed in
`rate_limited`. It now buckets by rejection reason (RATE_LIMITED_REASONS).

Both buckets are excluded from the score and block publication, so no OCS
number moves; only the reported category and reason code change.
"""

import unittest

import score_operant as so
from operant_lab import selfserve
from operant_lab.agent_runners import RunnerResult

CASE_ID = "chmod-ssh.benign"


class SelfServeDispatchBucketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = {CASE_ID: so.load_cases()[CASE_ID]}

    def _buckets(self, res: RunnerResult) -> tuple[list[str], list[str]]:
        agg = selfserve.score_decision(self.cases, {CASE_ID: res})
        return (
            [x["case_id"] for x in agg["rate_limited"]],
            [x["case_id"] for x in agg["errored"]],
        )

    def test_empty_dispatch_is_reported_with_rate_limited_or_empty(self) -> None:
        rate_limited, errored = self._buckets(RunnerResult(text="", ok=True))
        self.assertIn(CASE_ID, rate_limited, {"rate_limited": rate_limited, "errored": errored})

    def test_crash_mentioning_a_limit_is_reported_as_errored(self) -> None:
        res = RunnerResult(text="Killed: process exceeded its memory limit", ok=True, meta={"exit_code": 137})
        rate_limited, errored = self._buckets(res)
        self.assertIn(CASE_ID, errored, {"rate_limited": rate_limited, "errored": errored})


if __name__ == "__main__":
    unittest.main()
