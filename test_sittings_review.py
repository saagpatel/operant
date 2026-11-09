"""Acceptance tests for sealed sitting review and publication."""

from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from sittings.review import ReviewQueue


class ReviewQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.queue = ReviewQueue(self.root / "data")
        self.source = self.root / "source"
        self.source.mkdir()
        self.web = self.root / "web" / "data"
        self.bound = b"bound fixture bytes"
        self.digest = hashlib.sha256(self.bound).hexdigest()
        (self.source / "fixture.txt").write_bytes(self.bound)
        self.record = {
            "sitting_id": "sit_abc", "agent_label": "agent", "claimed_model": "model",
            "corpus": {"files": [{"path": "fixture.txt", "sha256": self.digest}]},
            "scorer": {"files": []}, "score": {"ocs": 0.5, "tpr": 0.7, "fpr": 0.2,
                                                   "n_scored": 1, "n_cases": 1,
                                                   "n_unanswered": 0, "band": "positive"},
            "model_binding": {"level": "unbound"},
        }
        statement = {"predicate": {"sealed_at": "2026-11-08T20:20:00Z",
                                     "model_binding": {"level": "unbound"}}}
        self.receipt = {"envelope": {"payload": base64.b64encode(
            json.dumps(statement).encode()).decode()}}
        self.passing = lambda receipt, **kwargs: SimpleNamespace(ok=True)
        self.failing = lambda receipt, **kwargs: SimpleNamespace(ok=False)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def approve(self, verifier=None):
        return self.queue.approve("sit_abc", web_data=self.web, verifier=verifier or self.passing,
                                  blob_source=self.source)

    def test_enqueue_approve_and_reapprove_publish_verified_bytes_once(self) -> None:
        self.assertEqual(self.queue.enqueue(self.record, self.receipt), "pending")
        self.assertEqual(self.queue.list("pending")[0]["status"]["state"], "pending")
        self.approve()
        self.approve()
        rows = json.loads((self.web / "rows.json").read_text())
        self.assertEqual(len(rows["rows"]), 1)
        self.assertEqual(rows["rows"][0]["sitting_id"], "sit_abc")
        self.assertEqual((self.web / "blobs" / self.digest).read_bytes(), self.bound)
        self.assertEqual(json.loads((self.web / "records/sit_abc.record.json").read_text()), self.record)
        self.assertEqual(json.loads((self.web / "receipts/sit_abc.receipt.json").read_text()), self.receipt)
        self.assertEqual(hashlib.sha256((self.web / "blobs" / self.digest).read_bytes()).hexdigest(),
                         self.digest)
        self.assertEqual(json.loads((self.web / "rows.json").read_text())["format"],
                         "operant.instruments-rows/v1")

    def test_failed_verifier_publishes_nothing(self) -> None:
        self.queue.enqueue(self.record, self.receipt)
        with self.assertRaisesRegex(ValueError, "verification failed"):
            self.approve(self.failing)
        self.assertFalse(self.web.exists())

    def test_unsealed_is_rejected_and_cannot_publish(self) -> None:
        self.assertEqual(self.queue.enqueue(self.record, None), "rejected")
        entry = self.queue.show("sit_abc")
        self.assertEqual(entry["status"]["reason"], "unsealed")
        self.assertNotIn("receipt", entry)
        with self.assertRaisesRegex(ValueError, "cannot be approved"):
            self.approve()
        self.assertFalse(self.web.exists())

    def test_reject_and_unpublish(self) -> None:
        self.queue.enqueue(self.record, self.receipt)
        self.queue.reject("sit_abc", "review declined")
        self.assertEqual(self.queue.show("sit_abc")["status"]["reason"], "review declined")
        self.queue.enqueue({**self.record, "sitting_id": "sit_def"}, self.receipt)
        self.queue.approve("sit_def", web_data=self.web, verifier=self.passing,
                           blob_source=self.source)
        self.queue.unpublish("sit_def", web_data=self.web)
        self.assertEqual(json.loads((self.web / "rows.json").read_text())["rows"], [])
        self.assertTrue((self.web / "blobs" / self.digest).exists())

    def test_traversal_refused_and_rows_remain_valid(self) -> None:
        with self.assertRaises(ValueError):
            self.queue.enqueue({**self.record, "sitting_id": "../escape"}, self.receipt)
        self.queue.enqueue(self.record, self.receipt)
        self.assertEqual(json.loads((self.root / "data/review/pending/sit_abc/status.json").read_text())[
            "state"], "pending")


if __name__ == "__main__":
    unittest.main()
