from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from datasets.scripts.generate_synthetic_corpus import (
    _build_cases,
    _docx_bytes,
    _materialize_specs,
    _scan_pdf,
    _text_pdf,
)
import evaluation.validate_dataset as validator_module
from evaluation.validate_dataset import DatasetValidationError, validate_repository


REPO_ROOT = Path(__file__).resolve().parents[2]
FROZEN_TEXT_IDENTITY_PATHS = (
    "datasets/generated/canonical_facts.jsonl",
    "datasets/generated/corpus_manifest.json",
    "datasets/generated/documents/distractor_maintenance_calendar.txt",
    "datasets/generated/documents/distractor_marketing_glossary.txt",
    "datasets/generated/documents/distractor_retired_draft.txt",
    "datasets/generated/documents/distractor_thermal_fixture.txt",
    "datasets/generated/documents/synthetic_reporting_boundary.txt",
    "datasets/scripts/generate_synthetic_corpus.py",
    "evaluation/frozen_cases.jsonl",
    "evaluation/freeze_manifest.json",
)


class DatasetContractTests(unittest.TestCase):
    def test_frozen_identity_inputs_require_lf_checkout(self) -> None:
        result = subprocess.run(
            ["git", "check-attr", "eol", "--", *FROZEN_TEXT_IDENTITY_PATHS],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        attributes = {
            line.partition(": eol: ")[0]: line.partition(": eol: ")[2]
            for line in result.stdout.splitlines()
        }

        self.assertEqual(attributes, {path: "lf" for path in FROZEN_TEXT_IDENTITY_PATHS})
        for relative_path in FROZEN_TEXT_IDENTITY_PATHS:
            self.assertNotIn(b"\r\n", (REPO_ROOT / relative_path).read_bytes(), relative_path)

    def test_committed_fixture_satisfies_full_contract(self) -> None:
        result = validate_repository(REPO_ROOT)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["dataset_label"], "VERIFIED_SYNTHETIC")
        self.assertEqual(result["counts"]["logical_documents"], 16)
        self.assertEqual(result["counts"]["answerable"], 32)
        self.assertEqual(result["counts"]["unanswerable"], 8)
        self.assertEqual(result["counts"]["dev"], 24)
        self.assertEqual(result["counts"]["test"], 16)
        self.assertEqual(result["counts"]["required_evidence"], 40)
        self.assertLess(result["closest_cross_split_pair"]["ratio"], 0.82)

    def test_generation_is_byte_deterministic(self) -> None:
        first_documents, first_facts = _materialize_specs()
        second_documents, second_facts = _materialize_specs()
        self.assertEqual(first_documents, second_documents)
        self.assertEqual(first_facts, second_facts)
        self.assertEqual(_build_cases(first_facts), _build_cases(second_facts))
        for first, second in zip(first_documents, second_documents, strict=True):
            if first["format"] == "pdf" and first["pdf_mode"] == "scan":
                first_payload = _scan_pdf(first["title"], first["sections"])
                second_payload = _scan_pdf(second["title"], second["sections"])
            elif first["format"] == "pdf":
                first_payload = _text_pdf(
                    first["title"], first["sections"], first["pdf_mode"] == "table"
                )
                second_payload = _text_pdf(
                    second["title"], second["sections"], second["pdf_mode"] == "table"
                )
            elif first["format"] == "docx":
                first_payload = _docx_bytes(first["title"], first["sections"])
                second_payload = _docx_bytes(second["title"], second["sections"])
            else:
                continue
            self.assertEqual(first_payload, second_payload, first["document_id"])

    def test_hash_tamper_is_rejected(self) -> None:
        real_sha256 = validator_module._sha256

        def changed_cases_hash(path: Path) -> str:
            if path.name == "frozen_cases.jsonl":
                return "0" * 64
            return real_sha256(path)

        with patch(
            "evaluation.validate_dataset._sha256", side_effect=changed_cases_hash
        ):
            with self.assertRaises(DatasetValidationError) as caught:
                validate_repository(REPO_ROOT)
            self.assertTrue(
                any("hash mismatch" in error for error in caught.exception.errors)
            )

    def test_gold_uses_exact_stable_locator_identity(self) -> None:
        facts = [
            json.loads(line)
            for line in (REPO_ROOT / "datasets" / "generated" / "canonical_facts.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line
        ]
        cases = [
            json.loads(line)
            for line in (REPO_ROOT / "evaluation" / "frozen_cases.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line
        ]
        fact_by_evidence = {fact["evidence_id"]: fact for fact in facts}
        required = [
            evidence for case in cases for evidence in case["gold"]["required_evidence"]
        ]
        self.assertEqual(len(required), 40)
        self.assertEqual(len({item["evidence_id"] for item in required}), 40)
        for evidence in required:
            fact = fact_by_evidence[evidence["evidence_id"]]
            self.assertEqual(
                evidence,
                {
                    "evidence_id": fact["evidence_id"],
                    "fact_id": fact["fact_id"],
                    **fact["locator"],
                },
            )


if __name__ == "__main__":
    unittest.main()
