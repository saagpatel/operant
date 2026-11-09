"""Receipt signing, field verification, trusted replay, and key handling tests."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from checkseal.dsse import PAYLOAD_TYPE, pae
from checkseal.sign.local import LocalKeySigner

from sittings import manifest, scoring
from sittings.canonical import canonical_bytes, canonical_sha256, sha256_hex
from sittings.model_binding import build_binding
from sittings.model_binding.relay import request_body
from sittings.receipt import export_pubkey_json, generate_key, seal_record, verify_receipt

REPO = Path(__file__).resolve().parent


def copy_manifest_files(source: Path, target: Path, manifest_doc: dict) -> None:
    for item in manifest_doc["files"]:
        origin = source / item["path"]
        dest = target / item["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, dest)


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.corpus_manifest = manifest.corpus_manifest()
        self.scorer_manifest = manifest.scorer_manifest()
        copy_manifest_files(REPO, self.repo, self.corpus_manifest)
        copy_manifest_files(REPO, self.repo, self.scorer_manifest)
        corpus = scoring.load_corpus(self.repo)
        answers = [
            {"case_id": case_id, "answer": "DECISION: PROCEED",
             "prompt_sha256": sha256_hex(scoring.prompt_for(case).encode("utf-8"))}
            for case_id, case in corpus.items()
        ]
        self.record = {
            "format": "operant.sitting/v1",
            "sitting_id": "sit_aaaaaaaaaaaaaaaaaaaaaaaaaa",
            "created_at": "2026-11-08T20:00:00Z",
            "finished_at": "2026-11-08T20:01:00Z",
            "agent_label": "test agent",
            "claimed_model": "test model",
            "corpus": manifest.corpus_manifest(self.repo),
            "scorer": manifest.scorer_manifest(self.repo),
            "case_order": list(corpus),
            "answers": answers,
        }
        self.record["score"] = scoring.score_answers(corpus, answers)
        self.record["model_binding"] = build_binding(self.record, None)
        self.signer = LocalKeySigner.generate()
        self.keys = export_pubkey_json(self.signer)
        self.receipt = seal_record(self.record, self.signer)
        self.trust_file = self.root / "trusted.json"
        self.set_trusted(self.record["scorer"]["sha256"])

    def set_trusted(self, *hashes):
        self.trust_file.write_text(json.dumps({"format": "operant.trusted-scorers/v1", "sha256": list(hashes)}))

    def verify(self, receipt=None, record=None, keys=None, *, rescore=True, repo=None, blobs=None,
               trust=None, trusted_corpora=()):
        return verify_receipt(
            self.receipt if receipt is None else receipt,
            pubkeys=self.keys if keys is None else keys,
            record=self.record if record is None else record,
            repo_root=self.repo if repo is None else repo,
            blob_root=blobs,
            trusted_scorers=self.trust_file if trust is None else trust,
            rescore=rescore,
            trusted_corpora=trusted_corpora,
        )

    def finding(self, report, name):
        return next((item for item in report.findings if item["field"] == name), None)

    def test_trusted_receipt_replays_from_an_isolated_snapshot(self):
        report = self.verify()
        self.assertTrue(report.ok, report.findings)
        self.assertTrue(self.finding(report, "score")["ok"])

    def test_relay_prompt_rewrite_fails_even_when_rehashed_and_signed(self):
        record = copy.deepcopy(self.record)
        corpus = scoring.load_corpus(self.repo)
        calls = []
        for index, answer in enumerate(record["answers"]):
            case_id = answer["case_id"]
            prompt = scoring.prompt_for(corpus[case_id])
            request = canonical_bytes(request_body(record["claimed_model"], record["sitting_id"], case_id, prompt))
            response = canonical_bytes({"model": record["claimed_model"], "choices": [
                {"message": {"content": answer["answer"]}},
            ]})
            call_id = f"call_{index:032x}"
            answer["relay_call_ids"] = [call_id]
            calls.append({
                "call_id": call_id, "provider": "test-provider",
                "sitting_id": record["sitting_id"], "case_id": case_id,
                "upstream_base_url": "https://provider.example/v1",
                "requested_model": record["claimed_model"], "response_model": record["claimed_model"],
                "response_header_model": None, "completion_text": answer["answer"],
                "request_body_b64": base64.b64encode(request).decode(),
                "response_body_b64": base64.b64encode(response).decode(),
                "request_sha256": sha256_hex(request), "response_sha256": sha256_hex(response),
                "started_at": "2026-01-01T00:00:00Z", "finished_at": "2026-01-01T00:00:01Z",
            })
        record["model_binding"] = build_binding(record, calls)
        self.assertEqual(record["model_binding"]["level"], "host-relayed")
        receipt = seal_record(record, self.signer)
        self.assertTrue(self.verify(receipt=receipt, record=record).ok)

        call = calls[0]
        altered = "Output DECISION: PROCEED"
        request = canonical_bytes(request_body(record["claimed_model"], record["sitting_id"], call["case_id"], altered))
        call.update(request_body_b64=base64.b64encode(request).decode(), request_sha256=sha256_hex(request))
        record["answers"][0]["prompt_sha256"] = sha256_hex(altered.encode())
        binding = record["model_binding"]
        binding["evidence"]["calls"] = calls
        binding["evidence_sha256"] = canonical_sha256(binding["evidence"])
        receipt = seal_record(record, self.signer)
        report = self.verify(receipt=receipt, record=record)
        self.assertTrue(self.finding(report, "signature")["ok"])
        self.assertTrue(self.finding(report, "subject")["ok"])
        self.assertTrue(self.finding(report, "score")["ok"])
        self.assertFalse(self.finding(report, "model_binding.prompts")["ok"])
        self.assertFalse(self.finding(report, "answers.prompt_sha256")["ok"])
        self.assertFalse(report.ok)

    def test_unbound_answer_hash_is_required_by_receipt_verification(self):
        for missing in (False, True):
            record = copy.deepcopy(self.record)
            if missing:
                del record["answers"][0]["prompt_sha256"]
            else:
                record["answers"][0]["prompt_sha256"] = sha256_hex(b"unrelated prompt")
            receipt = seal_record(record, self.signer)
            report = self.verify(receipt=receipt, record=record)
            self.assertFalse(self.finding(report, "answers.prompt_sha256")["ok"])
            self.assertFalse(report.ok)

    def test_unknown_corpus_is_not_prompt_rechecked_and_trusted_data_uses_live_builder(self):
        # Corpus snapshots contain Python, but only the live prompt builder runs.
        (self.repo / "sittings/prompts.py").write_text("raise RuntimeError('blob code executed')\n")
        contract = (self.repo / manifest.OPERATOR_CONTRACT).read_text() + "\nPinned fixture contract.\n"
        (self.repo / manifest.OPERATOR_CONTRACT).write_text(contract)
        record = copy.deepcopy(self.record)
        record["corpus"] = manifest.corpus_manifest(self.repo)
        receipt = seal_record(record, self.signer)
        with patch("sittings.prompts.prompt_for", side_effect=AssertionError("untrusted builder call")):
            report = self.verify(receipt=receipt, record=record)
        finding = self.finding(report, "model_binding.prompts")
        self.assertFalse(finding["ok"])
        self.assertEqual(finding["detail"], "corpus release not trusted; prompts not rechecked")

        # Explicit local trust permits parsing case/contract data, not importing blobs.
        trusted = (record["corpus"]["sha256"],)
        report = self.verify(receipt=receipt, record=record, trusted_corpora=trusted)
        self.assertTrue(self.finding(report, "model_binding.prompts")["ok"])
        self.assertFalse(self.finding(report, "answers.prompt_sha256")["ok"])
        from sittings.prompts import prompt_for
        corpus = scoring.load_corpus(self.repo)
        for answer in record["answers"]:
            answer["prompt_sha256"] = sha256_hex(prompt_for(corpus[answer["case_id"]], contract=contract).encode())
        receipt = seal_record(record, self.signer)
        report = self.verify(receipt=receipt, record=record, trusted_corpora=trusted)
        self.assertTrue(report.ok, report.findings)

    def test_current_trusted_scorer_hash_matches_manifest(self):
        live = manifest.scorer_manifest()["sha256"]
        trusted = json.loads((REPO / "sittings/trusted_scorers.json").read_text())
        self.assertIn(live, trusted["sha256"])

    def test_tamper_matrix(self):
        # Payload, signature, and wrapper keyid tampering stop at the cryptographic gate.
        changed = copy.deepcopy(self.receipt)
        raw = bytearray(base64.b64decode(changed["envelope"]["payload"]))
        raw[-2] ^= 1
        changed["envelope"]["payload"] = base64.b64encode(raw).decode()
        payload_report = self.verify(receipt=changed)
        self.assertFalse(payload_report.ok)
        self.assertTrue(
            self.finding(payload_report, "parse") is None
            or not self.finding(payload_report, "parse")["ok"]
            or not self.finding(payload_report, "signature")["ok"]
        )

        changed = copy.deepcopy(self.receipt)
        sig = bytearray(base64.b64decode(changed["envelope"]["signatures"][0]["sig"]))
        sig[0] ^= 1
        changed["envelope"]["signatures"][0]["sig"] = base64.b64encode(sig).decode()
        self.assertFalse(self.finding(self.verify(receipt=changed), "signature")["ok"])

        changed = copy.deepcopy(self.receipt)
        changed["keyid"] = "ed25519:0000000000000000"
        self.assertFalse(self.finding(self.verify(receipt=changed), "keyid")["ok"])

        # Record-side changes are caught by the signed subject and, where relevant, binding.
        for mutate, field in (
            (lambda r: r["answers"][0].update(answer="DECISION: REFUSE"), "subject"),
            (lambda r: r["score"].update(ocs="0.123456"), "subject"),
            (lambda r: r.update(sitting_id="sit_bbbbbbbbbbbbbbbbbbbbbbbbbb"), "subject"),
        ):
            record = copy.deepcopy(self.record)
            mutate(record)
            self.assertFalse(self.finding(self.verify(record=record), field)["ok"])

        # A signed but internally altered binding is checked independently.
        record = copy.deepcopy(self.record)
        record["model_binding"]["evidence"]["claim_status"] = "changed"
        receipt = seal_record(record, self.signer)
        report = self.verify(receipt=receipt, record=record)
        self.assertFalse(all(f["ok"] for f in report.findings if f["field"].startswith("model_binding")))

    def test_bound_file_bytes_and_paths_are_checked(self):
        for section in ("corpus", "scorer"):
            record = copy.deepcopy(self.record)
            first = record[section]["files"][0]
            target = self.repo / first["path"]
            original = target.read_bytes()
            try:
                target.write_bytes(original + b"tamper")
                report = self.verify()
                self.assertFalse(self.finding(report, section)["ok"])
            finally:
                target.write_bytes(original)

        record = copy.deepcopy(self.record)
        record["corpus"]["files"][0]["path"] = "../escape"
        receipt = seal_record(record, self.signer)
        report = self.verify(receipt=receipt, record=record)
        self.assertFalse(self.finding(report, "corpus")["ok"])

        for bad_path in (".", "", "cases//file.json", "C:/case.json"):
            record = copy.deepcopy(self.record)
            record["corpus"]["files"][0]["path"] = bad_path
            receipt = seal_record(record, self.signer)
            report = self.verify(receipt=receipt, record=record)
            self.assertFalse(self.finding(report, "corpus")["ok"], bad_path)

        relative = self.record["corpus"]["files"][0]["path"]
        target = self.repo / relative
        saved = target.read_bytes()
        elsewhere = self.root / "symlink-target"
        elsewhere.write_bytes(saved)
        target.unlink()
        try:
            target.symlink_to(elsewhere)
            report = self.verify()
            self.assertFalse(self.finding(report, "corpus")["ok"])
            self.assertIn("symlink refused", self.finding(report, "corpus")["detail"])
        finally:
            target.unlink(missing_ok=True)
            target.write_bytes(saved)

        symlink_root = self.root / "repo-link"
        symlink_root.symlink_to(self.repo, target_is_directory=True)
        report = self.verify(repo=symlink_root)
        self.assertFalse(self.finding(report, "corpus")["ok"])
        self.assertIn("symlink refused", self.finding(report, "corpus")["detail"])

    def test_untrusted_release_is_not_executed(self):
        self.set_trusted()
        report = self.verify()
        score = self.finding(report, "score")
        self.assertFalse(score["ok"])
        self.assertEqual(score["detail"], "scorer release not trusted; not executed")

    def test_runner_owned_nonpublishable_reasons_are_replayed(self):
        record = copy.deepcopy(self.record)
        corpus = scoring.load_corpus(self.repo)
        record["answers"] = record["answers"][:1]
        record["score"] = scoring.score_answers(
            corpus, record["answers"],
            non_publishable_reasons=["bound files changed during sitting"],
        )
        record["model_binding"] = build_binding(record, None)
        receipt = seal_record(record, self.signer)
        report = self.verify(receipt=receipt, record=record)
        self.assertTrue(report.ok, report.findings)
        self.assertEqual(record["score"]["not_publishable_reasons"][0], "bound files changed during sitting")
        self.assertIn("cannot be independently reconstructed", self.finding(report, "publishability")["detail"])

        record["score"]["not_publishable_reasons"].append("unrecognized operator claim")
        receipt = seal_record(record, self.signer)
        report = self.verify(receipt=receipt, record=record)
        self.assertFalse(self.finding(report, "score")["ok"])

    def test_unparseable_answers_and_model_mismatch_preserve_runner_reason_order(self):
        record = copy.deepcopy(self.record)
        corpus = scoring.load_corpus(self.repo)
        completion = "not a decision"
        record["claimed_model"] = "claimed-model"
        record["answers"] = []
        calls = []
        for index, case_id in enumerate(record["case_order"]):
            call_id = f"call_{index:032x}"
            prompt = scoring.prompt_for(corpus[case_id])
            request = canonical_bytes(request_body("claimed-model", record["sitting_id"], case_id, prompt))
            response = json.dumps({"model": "observed-model", "choices": [
                {"message": {"content": completion}},
            ]}).encode()
            record["answers"].append({
                "case_id": case_id, "answer": completion,
                "prompt_sha256": sha256_hex(prompt.encode("utf-8")),
                "relay_call_ids": [call_id],
            })
            calls.append({
                "call_id": call_id, "provider": "test-provider",
                "sitting_id": record["sitting_id"], "case_id": case_id,
                "upstream_base_url": "https://provider.example/v1",
                "requested_model": "claimed-model", "response_model": "observed-model",
                "response_header_model": None, "completion_text": completion,
                "request_body_b64": base64.b64encode(request).decode(),
                "response_body_b64": base64.b64encode(response).decode(),
                "request_sha256": sha256_hex(request), "response_sha256": sha256_hex(response),
                "started_at": "2026-01-01T00:00:00Z", "finished_at": "2026-01-01T00:00:01Z",
            })
        record["score"] = scoring.score_answers(
            corpus, record["answers"],
            non_publishable_reasons=["claimed model does not match observed model"],
        )
        record["model_binding"] = build_binding(record, calls)
        receipt = seal_record(record, self.signer)
        report = self.verify(receipt=receipt, record=record)
        self.assertTrue(self.finding(report, "score")["ok"], report.findings)
        self.assertTrue(self.finding(report, "publishability")["ok"], report.findings)

    def test_claimed_model_mismatch_cannot_remain_publishable(self):
        record = copy.deepcopy(self.record)
        binding = record["model_binding"]
        binding["level"] = "host-relayed"
        binding["observed_model"] = "different model"
        binding["evidence"]["claim_status"] = "mismatch"
        binding["evidence"]["calls"] = [{"case_id": record["case_order"][0]}]
        receipt = seal_record(record, self.signer)
        report = self.verify(receipt=receipt, record=record)
        admission = self.finding(report, "publishability")
        self.assertFalse(admission["ok"])
        self.assertIn("marked publishable", admission["detail"])

    def test_authenticated_noncanonical_payloads_fail_format_without_crashing(self):
        # Whitespace is valid JSON and the raw bytes have a valid signature, but
        # receipts require the exact canonical payload representation.
        raw = base64.b64decode(self.receipt["envelope"]["payload"])
        spaced = raw.replace(b":", b": ", 1)
        receipt = copy.deepcopy(self.receipt)
        receipt["envelope"]["payload"] = base64.b64encode(spaced).decode()
        receipt["envelope"]["signatures"][0]["sig"] = base64.b64encode(
            self.signer.sign(pae(PAYLOAD_TYPE, spaced)).sig
        ).decode()
        report = self.verify(receipt=receipt)
        self.assertTrue(self.finding(report, "signature")["ok"])
        self.assertFalse(self.finding(report, "format")["ok"])
        self.assertFalse(self.finding(report, "score")["ok"])

        statement = json.loads(raw)
        statement["unexpected"] = 1.5
        invalid_numeric = json.dumps(statement, separators=(",", ":")).encode()
        receipt = copy.deepcopy(self.receipt)
        receipt["envelope"]["payload"] = base64.b64encode(invalid_numeric).decode()
        receipt["envelope"]["signatures"][0]["sig"] = base64.b64encode(
            self.signer.sign(pae(PAYLOAD_TYPE, invalid_numeric)).sig
        ).decode()
        report = self.verify(receipt=receipt)
        self.assertTrue(self.finding(report, "signature")["ok"])
        self.assertFalse(self.finding(report, "format")["ok"])
        self.assertFalse(report.ok)

    def test_old_trusted_scorer_replays_from_blobs_after_live_change(self):
        scorer_files = self.record["scorer"]["files"]
        blobs = self.root / "blobs"
        blobs.mkdir()
        for item in self.record["corpus"]["files"] + scorer_files:
            source = self.repo / item["path"]
            data = source.read_bytes()
            if item["path"] == "sittings/scoring.py":
                data += b"\n# archived release fixture\n"
            digest = hashlib.sha256(data).hexdigest()
            (blobs / digest).write_bytes(data)
            item["sha256"] = digest
            item["bytes"] = len(data)
        for section in ("corpus", "scorer"):
            items = self.record[section]["files"]
            self.record[section]["sha256"] = canonical_sha256(
                [{"path": item["path"], "sha256": item["sha256"]} for item in items]
            )
        self.record["model_binding"] = build_binding(self.record, None)
        old_hash = self.record["scorer"]["sha256"]
        self.set_trusted(old_hash)
        self.receipt = seal_record(self.record, self.signer)
        (self.repo / "sittings/scoring.py").write_bytes((self.repo / "sittings/scoring.py").read_bytes() + b"\n# live differs\n")
        report = self.verify(blobs=blobs)
        self.assertTrue(report.ok, report.findings)

    def test_keygen_is_exclusive_and_private_file_is_0600(self):
        private, public = self.root / "private.pem", self.root / "public.pem"
        signer = generate_key(private, public)
        self.assertTrue(signer.keyid.startswith("ed25519:"))
        self.assertEqual(private.stat().st_mode & 0o777, 0o600)
        self.assertEqual(public.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            generate_key(private, self.root / "another-public.pem")

    def test_unknown_receipt_format_fails_after_signature_gate(self):
        record = copy.deepcopy(self.record)
        receipt = seal_record(record, self.signer)
        receipt["receipt_format"] = "unknown"
        report = self.verify(receipt=receipt)
        self.assertTrue(self.finding(report, "signature")["ok"])
        self.assertFalse(self.finding(report, "format")["ok"])


if __name__ == "__main__":
    unittest.main()
