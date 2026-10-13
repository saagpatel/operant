from __future__ import annotations

import unittest
import unittest.mock as mock

from operant_lab import selfserve
from operant_lab.agent_runners import RunnerResult


class SelfserveJudgeStatusTests(unittest.TestCase):
    def test_all_failed_partial_and_all_good_verdicts(self) -> None:
        cases = {f"case-{i}": {"id": f"case-{i}"} for i in range(3)}
        answers = {cid: RunnerResult("plan", True) for cid in cases}

        class StubJudge:
            DEFAULT_JUDGE_MODEL = "stub"
            rows: list[dict[str, object]] = []

            @classmethod
            def judge_one(cls, _case, _text, *, judge_model):
                return cls.rows.pop(0)

            @staticmethod
            def aggregate(rows):
                scored = [row for row in rows if row["score"] is not None]
                return {
                    "n": len(scored),
                    "mean_score": sum(row["score"] for row in scored) / len(scored),
                    "unparseable": len(rows) - len(scored),
                }

        fixtures = [
            ([{"score": None}] * 3, "judge_unavailable", 0, 3, 0.0),
            ([{"score": 1.0}, {"score": None}, {"score": 0.5}], "scored", 2, 1, 2 / 3),
            ([{"score": 1.0}] * 3, "scored", 3, 0, 1.0),
        ]
        for rows, status, n, unparseable, coverage in fixtures:
            with self.subTest(status=status, n=n):
                StubJudge.rows = list(rows)
                with mock.patch.object(selfserve, "_sibling", return_value=StubJudge):
                    result = selfserve.score_orchestration(
                        cases, answers, judge_model="stub", enabled=True
                    )
                self.assertEqual(result["status"], status)
                self.assertEqual(result["n"], n)
                self.assertEqual(result["unparseable"], unparseable)
                self.assertAlmostEqual(result["coverage"], coverage, places=3)


if __name__ == "__main__":
    unittest.main()
