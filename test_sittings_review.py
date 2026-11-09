"""Acceptance tests for sealed sitting review and publication."""

from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sittings.canonical import canonical_sha256
from sittings.review import ReviewQueue
from sittings.review import queue as queue_module


class ReviewQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.queue = ReviewQueue(self.root / "data")
        self.source = self.root / "source"
        self.source.mkdir()
        self.web = self.root / "web" / "data"
        self.bound = b"bound fixture bytes"
        self.digest = hashlib.sha256(self.bound).hexdigest()
        (self.source / "fixture.txt").write_bytes(self.bound)
        files = [{"path": "fixture.txt", "sha256": self.digest, "bytes": len(self.bound)}]
        manifest = {
            "sha256": canonical_sha256([{"path": "fixture.txt", "sha256": self.digest}]),
            "files": files,
        }
        self.record = {
            "sitting_id": "sit_abc", "agent_label": "agent", "claimed_model": "model",
            "corpus": manifest, "scorer": manifest,
            "score": {
                "ocs": "0.500000", "tpr": "0.700000", "fpr": "0.200000",
                "accuracy": "0.700000", "safe_correct_rate": "0.700000",
                "n_cases": 1, "n_scored": 1, "n_unparseable": 0,
                "n_unanswered": 0, "n_bypass_failures": 0, "band": "positive",
                "publishable": True, "not_publishable_reasons": [],
            },
            "model_binding": {
                "level": "unbound", "observed_model": None,
                "evidence": {"claim_status": "unknown"},
            },
        }
        statement = {"predicate": {"sealed_at": "2026-11-08T20:20:00Z",
                                     "model_binding": {"level": "unbound"}}}
        self.receipt = {"envelope": {"payload": base64.b64encode(
            json.dumps(statement).encode()).decode()}}
        self.passing = lambda receipt, **kwargs: SimpleNamespace(ok=True)
        self.failing = lambda receipt, **kwargs: SimpleNamespace(ok=False)
        self.pubkeys = {"format": "operant.sittings-pubkey/v1", "keys": [{
            "keyid": "ed25519:0123456789abcdef", "alg": "ed25519",
            "public_key_pem": "test pem", "spki_der_b64": "AA==",
        }]}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def approve(self, verifier=None):
        return self.queue.approve("sit_abc", web_data=self.web, verifier=verifier or self.passing,
                                  blob_source=self.source, pubkeys=self.pubkeys)

    def test_enqueue_approve_and_reapprove_publish_verified_bytes_once(self) -> None:
        self.assertEqual(self.queue.enqueue(self.record, self.receipt), "pending")
        self.assertEqual(self.queue.list("pending")[0]["status"]["state"], "pending")
        self.approve()
        self.assertEqual(self.queue.enqueue(self.record, self.receipt), "approved")
        self.approve()
        rows = json.loads((self.web / "rows.json").read_text())
        self.assertEqual(len(rows["rows"]), 1)
        row = rows["rows"][0]
        self.assertEqual(row["sitting_id"], "sit_abc")
        self.assertEqual(row["observed_model"], None)
        self.assertEqual(row["claim_status"], "unknown")
        self.assertTrue((self.web / "sittings-pubkey.json").exists())
        self.assertEqual(json.loads((self.web / "sittings-pubkey.json").read_text()), self.pubkeys)
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

    def test_rejection_during_verification_prevents_stale_approval(self) -> None:
        self.queue.enqueue(self.record, self.receipt)
        verifying = threading.Event()
        release = threading.Event()

        def verify(*args, **kwargs):
            verifying.set()
            if not release.wait(timeout=10):
                raise TimeoutError("verification test was not released")
            return SimpleNamespace(ok=True)

        with ThreadPoolExecutor(max_workers=2) as pool:
            approval = pool.submit(self.approve, verify)
            try:
                self.assertTrue(verifying.wait(timeout=10))
                other_queue = ReviewQueue(self.root / "data")
                rejection = pool.submit(other_queue.reject, "sit_abc", "review declined")
                # Rejection completes before verification, so approval must refuse.
                rejection.result(timeout=10)
                self.assertEqual(self.queue.show("sit_abc")["status"]["state"], "rejected")
            finally:
                release.set()
            with self.assertRaisesRegex(ValueError, "rejected sitting cannot be approved"):
                approval.result(timeout=10)

        status = self.queue.show("sit_abc")["status"]
        self.assertEqual(status["state"], "rejected")
        self.assertEqual(status["reason"], "review declined")
        self.assertEqual(self.queue.list("approved"), [])
        self.assertFalse((self.web / "rows.json").exists())
        self.assertFalse((self.web / "records/sit_abc.record.json").exists())
        self.assertFalse((self.web / "receipts/sit_abc.receipt.json").exists())

    def test_changed_record_during_verification_cannot_publish(self) -> None:
        self.queue.enqueue(self.record, self.receipt)

        def verify(*args, **kwargs):
            path = self.queue.root / "pending/sit_abc/record.json"
            queue_module._atomic_json(path, {**self.record, "agent_label": "changed"})
            return SimpleNamespace(ok=True)

        with self.assertRaisesRegex(ValueError, "sitting changed during receipt verification"):
            self.approve(verify)
        self.assertEqual(self.queue.show("sit_abc")["status"]["state"], "pending")
        self.assertFalse(self.web.exists())

    def test_key_documents_merge_and_deduplicate_before_verification(self) -> None:
        self.queue.enqueue(self.record, self.receipt)
        second = json.loads(json.dumps(self.pubkeys))
        second["keys"][0]["keyid"] = "ed25519:fedcba9876543210"
        verifier = Mock(return_value=SimpleNamespace(ok=True))
        self.queue.approve("sit_abc", web_data=self.web, verifier=verifier,
                           blob_source=self.source, pubkeys=[self.pubkeys, second, self.pubkeys])
        expected = {"format": self.pubkeys["format"],
                    "keys": self.pubkeys["keys"] + second["keys"]}
        self.assertEqual(verifier.call_args.kwargs["pubkeys"], expected)
        self.assertEqual(json.loads((self.web / "sittings-pubkey.json").read_text()), expected)

    def test_invalid_or_conflicting_key_documents_fail_before_verification(self) -> None:
        self.queue.enqueue(self.record, self.receipt)
        conflict = json.loads(json.dumps(self.pubkeys))
        conflict["keys"][0]["spki_der_b64"] = "AQ=="
        for invalid in (None, [], {}, {"format": self.pubkeys["format"], "keys": [{}]},
                        [self.pubkeys, conflict]):
            with self.subTest(pubkeys=invalid):
                verifier = Mock()
                with self.assertRaises(ValueError):
                    self.queue.approve("sit_abc", web_data=self.web, verifier=verifier,
                                       blob_source=self.source, pubkeys=invalid)
                verifier.assert_not_called()
                self.assertFalse(self.web.exists())

    def test_conflicting_published_key_cannot_overwrite_publication(self) -> None:
        self.queue.enqueue(self.record, self.receipt)
        self.approve()
        rows_before = (self.web / "rows.json").read_bytes()
        keys_before = (self.web / "sittings-pubkey.json").read_bytes()
        conflict = json.loads(json.dumps(self.pubkeys))
        conflict["keys"][0]["public_key_pem"] = "different pem"
        self.queue.enqueue({**self.record, "sitting_id": "sit_def"}, self.receipt)
        with self.assertRaisesRegex(ValueError, "conflicting"):
            self.queue.approve("sit_def", web_data=self.web, verifier=self.passing,
                               blob_source=self.source, pubkeys=conflict)
        self.assertEqual((self.web / "rows.json").read_bytes(), rows_before)
        self.assertEqual((self.web / "sittings-pubkey.json").read_bytes(), keys_before)
        self.assertFalse((self.web / "receipts/sit_def.receipt.json").exists())

    def test_concurrent_approvals_preserve_all_rows_and_keys(self) -> None:
        count = 8
        ready = threading.Barrier(count)
        for index in range(count):
            self.queue.enqueue({**self.record, "sitting_id": f"sit_{index}"}, self.receipt)

        def verify(*args, **kwargs):
            ready.wait(timeout=10)
            return SimpleNamespace(ok=True)

        def approve(index):
            keys = json.loads(json.dumps(self.pubkeys))
            keys["keys"][0]["keyid"] = f"ed25519:{index:016x}"
            # Separate queue objects model separate CLI invocations sharing publication data.
            return ReviewQueue(self.root / "data").approve(
                f"sit_{index}", web_data=self.web, verifier=verify,
                blob_source=self.source, pubkeys=keys,
            )

        atomic_json = queue_module._atomic_json

        def delayed_write(path, value):
            if path.name == "rows.json":
                # Widen the lost-update window so unlocked read/modify/write regresses reliably.
                time.sleep(0.02)
            atomic_json(path, value)

        with (patch.object(queue_module, "_atomic_json", side_effect=delayed_write),
              ThreadPoolExecutor(max_workers=count) as pool):
            rows = list(pool.map(approve, range(count)))
        published = json.loads((self.web / "rows.json").read_text())["rows"]
        self.assertCountEqual([row["sitting_id"] for row in published],
                              [row["sitting_id"] for row in rows])
        keys = json.loads((self.web / "sittings-pubkey.json").read_text())["keys"]
        self.assertCountEqual([key["keyid"] for key in keys],
                              [f"ed25519:{index:016x}" for index in range(count)])
        self.assertEqual(len(self.queue.list("approved")), count)

    def test_unpublish_waits_for_in_progress_publication(self) -> None:
        self.queue.enqueue(self.record, self.receipt)
        self.approve()
        self.queue.enqueue({**self.record, "sitting_id": "sit_def"}, self.receipt)
        publishing = threading.Event()
        unpublishing = threading.Event()
        release = threading.Event()
        atomic_json = queue_module._atomic_json
        flock = queue_module.fcntl.flock

        def pause_publication(path, value):
            if path.name == "rows.json" and len(value["rows"]) == 2:
                publishing.set()
                if not release.wait(timeout=10):
                    raise TimeoutError("publication test was not released")
            atomic_json(path, value)

        def observe_lock(lock, operation):
            if publishing.is_set() and operation == queue_module.fcntl.LOCK_EX:
                unpublishing.set()
            return flock(lock, operation)

        with (patch.object(queue_module, "_atomic_json", side_effect=pause_publication),
              patch.object(queue_module.fcntl, "flock", side_effect=observe_lock),
              ThreadPoolExecutor(max_workers=2) as pool):
            publish = pool.submit(self.queue.approve, "sit_def", web_data=self.web,
                                  verifier=self.passing, blob_source=self.source, pubkeys=self.pubkeys)
            try:
                self.assertTrue(publishing.wait(timeout=10))
                unpublish = pool.submit(self.queue.unpublish, "sit_abc", web_data=self.web)
                self.assertTrue(unpublishing.wait(timeout=10))
                self.assertFalse(unpublish.done())
            finally:
                release.set()
            publish.result(timeout=10)
            unpublish.result(timeout=10)
        rows = json.loads((self.web / "rows.json").read_text())["rows"]
        self.assertEqual([row["sitting_id"] for row in rows], ["sit_def"])

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
                           blob_source=self.source, pubkeys=self.pubkeys)
        self.queue.unpublish("sit_def", web_data=self.web)
        self.assertEqual(json.loads((self.web / "rows.json").read_text())["rows"], [])
        self.assertTrue((self.web / "blobs" / self.digest).exists())

    def test_traversal_refused_and_rows_remain_valid(self) -> None:
        with self.assertRaises(ValueError):
            self.queue.enqueue({**self.record, "sitting_id": "../escape"}, self.receipt)
        self.queue.enqueue(self.record, self.receipt)
        self.assertEqual(json.loads((self.root / "data/review/pending/sit_abc/status.json").read_text())[
            "state"], "pending")

    def test_nonpublishable_claim_mismatch_cannot_be_approved(self) -> None:
        record = json.loads(json.dumps(self.record))
        record["score"]["publishable"] = False
        record["score"]["not_publishable_reasons"] = ["claimed model differs from observed model"]
        record["model_binding"] = {
            "level": "host-relayed", "observed_model": "observed-model",
            "evidence": {"claim_status": "mismatch"},
        }
        self.queue.enqueue(record, self.receipt)
        with self.assertRaisesRegex(ValueError, "claimed model differs"):
            self.queue.approve("sit_abc", web_data=self.web, verifier=self.passing,
                               blob_source=self.source, pubkeys=self.pubkeys)
        self.assertFalse(self.web.exists())

    def test_symlinked_source_is_refused(self) -> None:
        target = self.source / "target.txt"
        target.write_bytes(self.bound)
        (self.source / "fixture.txt").unlink()
        (self.source / "fixture.txt").symlink_to(target)
        self.queue.enqueue(self.record, self.receipt)
        with self.assertRaisesRegex(ValueError, "symlink refused"):
            self.approve()
        self.assertFalse((self.web / "rows.json").exists())
        self.assertFalse((self.web / "receipts/sit_abc.receipt.json").exists())

    def test_rows_sort_decimal_rates_and_keep_binding_summary(self) -> None:
        first = json.loads(json.dumps(self.record))
        first["score"]["ocs"] = "0.900000"
        first["model_binding"] = {
            "level": "host-relayed", "observed_model": "reported-model",
            "evidence": {"claim_status": "match"},
        }
        second = json.loads(json.dumps(self.record))
        second["sitting_id"] = "sit_def"
        second["score"]["ocs"] = "0.100000"
        self.queue.enqueue(first, self.receipt)
        self.queue.enqueue(second, self.receipt)
        for record in (first, second):
            self.queue.approve(record["sitting_id"], web_data=self.web, verifier=self.passing,
                               blob_source=self.source, pubkeys=self.pubkeys)
        rows = json.loads((self.web / "rows.json").read_text())["rows"]
        self.assertEqual([row["sitting_id"] for row in rows], ["sit_abc", "sit_def"])
        self.assertEqual(rows[0]["observed_model"], "reported-model")
        self.assertEqual(rows[0]["claim_status"], "match")


if __name__ == "__main__":
    unittest.main()
