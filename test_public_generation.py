#!/usr/bin/env python3
"""Adversarial tests for exact public artifact generations."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from operant_lab import export, public_generation


class PublicGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.public = Path(self.temporary.name) / "public"
        self.public.mkdir()
        for name in public_generation.PUBLIC_CORE_FILES:
            path = self.public / name
            path.write_text(f"{name}: generation A\n", encoding="utf-8")
        cards = self.public / "model-cards"
        cards.mkdir()
        (cards / "active.json").write_text(
            '{"run_family":"active"}\n',
            encoding="utf-8",
        )
        public_generation.write_generation_manifest(self.public)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_complete_generation_validates(self) -> None:
        self.assertEqual(
            [],
            public_generation.validate_generation_manifest(self.public),
        )
        manifest = json.loads(
            (
                self.public / public_generation.GENERATION_MANIFEST_NAME
            ).read_text()
        )
        self.assertEqual(
            len(public_generation.PUBLIC_CORE_FILES) + 1,
            manifest["artifact_count"],
        )
        self.assertEqual("UNKNOWN", manifest["authorship"])
        self.assertEqual("UNKNOWN", manifest["external_immutability"])

    def test_missing_legacy_marker_fails_closed(self) -> None:
        (
            self.public / public_generation.GENERATION_MANIFEST_NAME
        ).unlink()
        self.assertEqual(
            ["generation manifest: missing regular commit marker"],
            public_generation.validate_generation_manifest(self.public),
        )

    def test_cross_generation_and_manifest_only_swaps_fail(self) -> None:
        old_readme = (self.public / "README.md").read_bytes()
        old_manifest = (
            self.public / public_generation.GENERATION_MANIFEST_NAME
        ).read_bytes()
        public_generation.write_text_atomic(
            self.public / "README.md",
            "README.md: generation B\n",
        )
        public_generation.write_generation_manifest(self.public)
        new_manifest = (
            self.public / public_generation.GENERATION_MANIFEST_NAME
        ).read_bytes()

        (self.public / "README.md").write_bytes(old_readme)
        self.assertTrue(
            public_generation.validate_generation_manifest(self.public)
        )
        (self.public / "README.md").write_text(
            "README.md: generation B\n",
            encoding="utf-8",
        )
        (
            self.public / public_generation.GENERATION_MANIFEST_NAME
        ).write_bytes(old_manifest)
        self.assertTrue(
            public_generation.validate_generation_manifest(self.public)
        )
        (
            self.public / public_generation.GENERATION_MANIFEST_NAME
        ).write_bytes(new_manifest)
        self.assertEqual(
            [],
            public_generation.validate_generation_manifest(self.public),
        )

    def test_add_delete_truncate_and_nested_extra_fail(self) -> None:
        extra_card = self.public / "model-cards" / "extra.json"
        extra_card.write_text("{}\n", encoding="utf-8")
        self.assertTrue(
            public_generation.validate_generation_manifest(self.public)
        )
        extra_card.unlink()
        extra = self.public / "unexpected.txt"
        extra.write_text("extra", encoding="utf-8")
        self.assertTrue(
            public_generation.validate_generation_manifest(self.public)
        )
        extra.unlink()
        target = self.public / "methodology.md"
        original = target.read_bytes()
        target.write_bytes(original[:3])
        self.assertTrue(
            public_generation.validate_generation_manifest(self.public)
        )
        target.write_bytes(original)
        target.unlink()
        self.assertTrue(
            public_generation.validate_generation_manifest(self.public)
        )
        target.write_bytes(original)
        nested = self.public / "model-cards" / "nested"
        nested.mkdir()
        (nested / "extra.json").write_text("{}\n", encoding="utf-8")
        self.assertTrue(
            public_generation.validate_generation_manifest(self.public)
        )

    def test_symlink_and_noncanonical_manifest_paths_fail(self) -> None:
        link = self.public / "model-cards" / "escape.json"
        link.symlink_to(self.public / "README.md")
        self.assertTrue(
            public_generation.validate_generation_manifest(self.public)
        )
        link.unlink()
        manifest_path = self.public / public_generation.GENERATION_MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text())
        manifest["artifacts"]["../escape.json"] = {
            "sha256": "0" * 64,
            "bytes": 1,
        }
        manifest["artifact_count"] += 1
        manifest_path.write_text(json.dumps(manifest) + "\n")
        self.assertIn(
            "generation manifest: artifact map is unsafe",
            public_generation.validate_generation_manifest(self.public),
        )

    def test_malformed_types_fail_without_raising(self) -> None:
        manifest_path = self.public / public_generation.GENERATION_MANIFEST_NAME
        malformed_values = [
            [],
            {"schema": 7},
            {
                "schema": public_generation.GENERATION_SCHEMA,
                "artifact_count": True,
                "artifacts": {"README.md": {"sha256": 7, "bytes": 1e300}},
                "generation_sha256": 10**1000,
                "commit_semantics": public_generation.GENERATION_COMMIT_SEMANTICS,
                "authorship": "UNKNOWN",
                "external_immutability": "UNKNOWN",
            },
        ]
        for value in malformed_values:
            with self.subTest(value_type=type(value).__name__):
                manifest_path.write_text(json.dumps(value) + "\n")
                self.assertTrue(
                    public_generation.validate_generation_manifest(self.public)
                )

    def test_interrupted_update_leaves_old_marker_invalid(self) -> None:
        public_generation.write_text_atomic(
            self.public / "README.md",
            "interrupted new bytes\n",
        )
        errors = public_generation.validate_generation_manifest(self.public)
        self.assertTrue(
            any("artifact bytes changed" in error for error in errors),
            errors,
        )

    def test_export_wrapper_serializes_concurrent_writers(self) -> None:
        active = 0
        maximum_active = 0
        active_lock = threading.Lock()

        def fake_export(
            source_results: Path,
            out_dir: Path,
            *,
            lab_runs_dir: Path | None,
            lab_labels: set[str] | None,
        ) -> dict[str, object]:
            nonlocal active, maximum_active
            with active_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                time.sleep(0.03)
                return {"out_dir": str(out_dir)}
            finally:
                with active_lock:
                    active -= 1

        errors: list[BaseException] = []

        def run_export() -> None:
            try:
                export.export_public_artifacts(Path("source"), self.public)
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        with mock.patch.object(
            export,
            "_export_public_artifacts_locked",
            side_effect=fake_export,
        ):
            threads = [threading.Thread(target=run_export) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual([], errors)
        self.assertEqual(1, maximum_active)


if __name__ == "__main__":
    unittest.main()
