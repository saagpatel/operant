#!/usr/bin/env python3
"""Receipt-lineage integrity and fail-closed behavior tests."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from operant_lab import lineage

CORE = "a" * 64


def _report(label: str, case_id: str) -> dict[str, object]:
    return {
        "manifest": {
            "run_label": label,
            "case_id": case_id,
            "manifest_core_sha256": CORE,
        },
        "parse_status": "ok",
        "final_answer": "private answer",
    }


def _path(root: Path, label: str, case_id: str) -> Path:
    return root / "lab" / "runs" / label / f"{case_id}.json"


class ReceiptLineageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _publish(self, label: str, case_id: str) -> Path:
        path = _path(self.root, label, case_id)
        return lineage.publish_receipt_with_lineage(
            self.root,
            path,
            _report(label, case_id),
        )

    def test_historical_receipts_are_presence_baseline_only(self) -> None:
        path = _path(self.root, "legacy", "case-1")
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(_report("legacy", "case-1"), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        lineage.initialize_receipt_lineage(self.root)
        self.assertEqual([], lineage.validate_receipt_lineage(self.root))
        baseline = json.loads(
            (self.root / lineage.LINEAGE_ROOT / lineage.BASELINE_NAME).read_text()
        )
        self.assertEqual(
            "PRESENCE_AT_LOCAL_ACTIVATION_ONLY",
            baseline["claim_boundary"],
        )
        self.assertEqual("NOT_PROVEN", baseline["history_immutability"])
        self.assertNotIn("receipt_lineage", json.loads(path.read_text()))

    def test_new_receipt_is_chained_and_checkpoint_is_ancestor(self) -> None:
        first = self._publish("run", "case-1")
        checkpoint = lineage.lineage_checkpoint(self.root)
        self._publish("run", "case-2")
        self.assertEqual([], lineage.validate_receipt_lineage(self.root))
        self.assertEqual(
            [],
            lineage.validate_lineage_checkpoint(self.root, checkpoint),
        )
        persisted = json.loads(first.read_text())
        self.assertEqual(
            "operant-receipt-lineage-link.v1",
            persisted["receipt_lineage"]["schema"],
        )

    def test_uncheckpointed_tail_removal_is_explicitly_not_detectable(self) -> None:
        self._publish("run", "case-1")
        checkpoint = lineage.lineage_checkpoint(self.root)
        tail = self._publish("run", "case-2")
        journal = self.root / lineage.LINEAGE_ROOT / lineage.JOURNAL_NAME
        lines = journal.read_bytes().splitlines(keepends=True)
        tail.unlink()
        journal.write_bytes(b"".join(lines[:-1]))
        self.assertEqual([], lineage.validate_receipt_lineage(self.root))
        self.assertEqual(
            [],
            lineage.validate_lineage_checkpoint(self.root, checkpoint),
        )

    def test_checkpoint_types_fail_closed(self) -> None:
        self._publish("run", "case-1")
        checkpoint = lineage.lineage_checkpoint(self.root)
        checkpoint["entry_sha256"] = int("1" * 64)
        self.assertTrue(
            lineage.validate_lineage_checkpoint(self.root, checkpoint)
        )

    def test_deletion_and_substitution_fail_closed(self) -> None:
        path = self._publish("run", "case-1")
        path.unlink()
        self.assertIn(
            "receipt lineage journal receipt is missing",
            lineage.validate_receipt_lineage(self.root),
        )
        self.assertEqual(
            "receipt_lineage_invalid",
            lineage.receipt_lineage_block_reason(self.root),
        )

        self.temporary.cleanup()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        path = self._publish("run", "case-1")
        data = json.loads(path.read_text())
        data["final_answer"] = "substituted"
        path.write_text(json.dumps(data, sort_keys=True) + "\n")
        self.assertIn(
            "receipt lineage journal receipt bytes changed",
            lineage.validate_receipt_lineage(self.root),
        )

    def test_journal_reorder_duplicate_and_partial_tail_are_invalid(self) -> None:
        self._publish("run", "case-1")
        self._publish("run", "case-2")
        journal = self.root / lineage.LINEAGE_ROOT / lineage.JOURNAL_NAME
        original = journal.read_bytes()
        lines = original.splitlines(keepends=True)
        journal.write_bytes(lines[0] + lines[2] + lines[1])
        self.assertTrue(lineage.validate_receipt_lineage(self.root))
        journal.write_bytes(lines[0] + lines[1] + lines[1] + lines[2])
        self.assertTrue(lineage.validate_receipt_lineage(self.root))
        journal.write_bytes(original[:-1])
        self.assertIn(
            "receipt lineage journal framing is invalid",
            lineage.validate_receipt_lineage(self.root),
        )

    def test_bool_sequence_is_invalid_even_with_recomputed_hash(self) -> None:
        self._publish("run", "case-1")
        journal = self.root / lineage.LINEAGE_ROOT / lineage.JOURNAL_NAME
        entries = [
            json.loads(line)
            for line in journal.read_text(encoding="utf-8").splitlines()
        ]
        entries[1]["sequence"] = True
        entries[1]["entry_sha256"] = lineage._entry_hash(entries[1])
        journal.write_text(
            "".join(
                json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"
                for entry in entries
            ),
            encoding="utf-8",
        )
        self.assertIn(
            "receipt lineage journal chain is invalid",
            lineage.validate_receipt_lineage(self.root),
        )

    def test_append_failure_leaves_detectable_orphan_and_blocks_next_write(
        self,
    ) -> None:
        with mock.patch.object(
            lineage,
            "_append_journal_entry",
            side_effect=OSError("simulated append failure"),
        ):
            with self.assertRaises(OSError):
                self._publish("run", "case-1")
        self.assertIn(
            "receipt lineage contains an orphan or untracked receipt",
            lineage.validate_receipt_lineage(self.root),
        )
        with self.assertRaisesRegex(RuntimeError, "invalid lineage"):
            self._publish("run", "case-2")

    def test_concurrent_writers_form_one_strict_chain(self) -> None:
        errors: list[BaseException] = []

        def publish(index: int) -> None:
            try:
                self._publish("parallel", f"case-{index}")
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=publish, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual([], errors)
        self.assertEqual([], lineage.validate_receipt_lineage(self.root))
        journal = self.root / lineage.LINEAGE_ROOT / lineage.JOURNAL_NAME
        entries = [
            json.loads(line)
            for line in journal.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(list(range(9)), [entry["sequence"] for entry in entries])
        self.assertEqual(9, len({entry["entry_sha256"] for entry in entries}))

    def test_path_aliases_and_traversal_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "canonical"):
            lineage.publish_receipt_with_lineage(
                self.root,
                _path(self.root, "run", "a_b"),
                _report("run", "a/b"),
            )
        with self.assertRaisesRegex(ValueError, "outside"):
            lineage.publish_receipt_with_lineage(
                self.root,
                self.root / "outside.json",
                _report("run", "case-1"),
            )


if __name__ == "__main__":
    unittest.main()
