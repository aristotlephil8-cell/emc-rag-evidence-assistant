"""Validate the frozen public synthetic corpus and its evaluation contract."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


EXPECTED_COUNTS = {
    "logical_documents": 16,
    "primary_documents": 12,
    "distractor_documents": 4,
    "pdf_documents": 6,
    "pdf_scan": 2,
    "pdf_table": 2,
    "pdf_text": 2,
    "docx_documents": 5,
    "txt_documents": 5,
    "canonical_facts": 40,
    "cases": 40,
    "answerable": 32,
    "unanswerable": 8,
    "dev": 24,
    "test": 16,
    "required_evidence": 40,
}


class DatasetValidationError(ValueError):
    """Raised when one or more frozen-dataset invariants are violated."""

    def __init__(self, errors: Iterable[str]):
        self.errors = list(errors)
        super().__init__("\n".join(self.errors))


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        try:
            rows.append(json.loads(raw_line))
        except json.JSONDecodeError as exc:
            raise DatasetValidationError(
                [f"{path}:{line_number}: invalid JSON: {exc}"]
            ) from exc
    return rows


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalized_question(question: str) -> str:
    question = question.replace("[合成题]", "").casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", question)


def _check_file_shape(document: dict[str, Any], path: Path, errors: list[str]) -> None:
    if not path.is_file():
        errors.append(f"missing document artifact: {path}")
        return
    payload = path.read_bytes()
    file_format = document.get("format")
    if file_format == "pdf":
        if not payload.startswith(b"%PDF-") or not payload.rstrip().endswith(b"%%EOF"):
            errors.append(f"invalid PDF envelope: {path}")
        mode = document.get("pdf_mode")
        has_image = b"/Subtype /Image" in payload
        has_font = b"/Type /Font" in payload
        if mode == "scan" and (not has_image or has_font):
            errors.append(f"scan PDF must be image-only with no font resource: {path}")
        if mode in {"text", "table"} and not has_font:
            errors.append(f"{mode} PDF must expose a text font resource: {path}")
        if mode == "table" and b" re S" not in payload:
            errors.append(f"table PDF must contain vector table rectangles: {path}")
    elif file_format == "docx":
        try:
            with zipfile.ZipFile(path) as archive:
                required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
                if not required.issubset(set(archive.namelist())):
                    errors.append(f"DOCX package is missing required parts: {path}")
                document_xml = archive.read("word/document.xml").decode("utf-8")
                if "SYNTHETIC TRAINING FIXTURE" not in document_xml:
                    errors.append(f"DOCX is missing synthetic notice: {path}")
        except (zipfile.BadZipFile, KeyError, UnicodeDecodeError) as exc:
            errors.append(f"invalid DOCX package {path}: {exc}")
    elif file_format == "txt":
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            errors.append(f"TXT must be UTF-8 {path}: {exc}")
        else:
            if "合成" not in text and "SYNTHETIC" not in text:
                errors.append(f"TXT is missing an explicit synthetic label: {path}")


def validate_repository(repo_root: Path) -> dict[str, Any]:
    """Validate hashes, exact counts, stable locators, and split isolation.

    Returns a compact proof summary.  All failures are accumulated and raised
    together to make freeze changes easy to audit.
    """

    repo_root = repo_root.resolve()
    corpus_path = repo_root / "datasets" / "generated" / "corpus_manifest.json"
    facts_path = repo_root / "datasets" / "generated" / "canonical_facts.jsonl"
    cases_path = repo_root / "evaluation" / "frozen_cases.jsonl"
    freeze_path = repo_root / "evaluation" / "freeze_manifest.json"
    required_paths = [corpus_path, facts_path, cases_path, freeze_path]
    missing = [
        f"missing required file: {path}"
        for path in required_paths
        if not path.is_file()
    ]
    if missing:
        raise DatasetValidationError(missing)

    errors: list[str] = []
    corpus = _load_json(corpus_path)
    facts = _load_jsonl(facts_path)
    cases = _load_jsonl(cases_path)
    freeze = _load_json(freeze_path)

    if corpus.get("dataset_label") != "VERIFIED_SYNTHETIC":
        errors.append("corpus dataset_label must be VERIFIED_SYNTHETIC")
    if freeze.get("dataset_label") != "VERIFIED_SYNTHETIC":
        errors.append("freeze dataset_label must be VERIFIED_SYNTHETIC")
    if corpus.get("corpus_version") != freeze.get("corpus_version"):
        errors.append("corpus_version differs between corpus and freeze manifests")

    documents = corpus.get("documents", [])
    if len(documents) != EXPECTED_COUNTS["logical_documents"]:
        errors.append(f"logical document count is {len(documents)}, expected 16")
    document_ids = [document.get("document_id") for document in documents]
    if len(document_ids) != len(set(document_ids)):
        errors.append("document_id values must be unique")
    filenames = [document.get("filename") for document in documents]
    if len(filenames) != len(set(filenames)):
        errors.append("document filenames must be unique")

    role_counts = Counter(document.get("role") for document in documents)
    format_counts = Counter(document.get("format") for document in documents)
    pdf_mode_counts = Counter(
        document.get("pdf_mode")
        for document in documents
        if document.get("format") == "pdf"
    )
    observed_document_counts = {
        "logical_documents": len(documents),
        "primary_documents": role_counts["primary"],
        "distractor_documents": role_counts["distractor"],
        "pdf_documents": format_counts["pdf"],
        "pdf_scan": pdf_mode_counts["scan"],
        "pdf_table": pdf_mode_counts["table"],
        "pdf_text": pdf_mode_counts["text"],
        "docx_documents": format_counts["docx"],
        "txt_documents": format_counts["txt"],
        "canonical_facts": len(facts),
    }
    for key, observed in observed_document_counts.items():
        if observed != EXPECTED_COUNTS[key]:
            errors.append(f"{key} is {observed}, expected {EXPECTED_COUNTS[key]}")
        if corpus.get("counts", {}).get(key) != EXPECTED_COUNTS[key]:
            errors.append(
                f"corpus manifest frozen count {key} is not {EXPECTED_COUNTS[key]}"
            )

    expected_artifact_paths: set[str] = set()
    document_by_id: dict[str, dict[str, Any]] = {}
    section_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for document in documents:
        document_id = document.get("document_id")
        if not isinstance(document_id, str) or not re.fullmatch(
            r"syn-[a-z0-9-]+", document_id
        ):
            errors.append(f"invalid stable document_id: {document_id!r}")
            continue
        document_by_id[document_id] = document
        artifact = document.get("artifact", {})
        relative_path = artifact.get("path")
        if not isinstance(relative_path, str):
            errors.append(f"document {document_id} is missing artifact.path")
            continue
        if (
            relative_path.startswith(("tmp/", ".tmp/"))
            or "/tmp/" in relative_path
            or "/.tmp/" in relative_path
        ):
            errors.append(f"forbidden temporary path in manifest: {relative_path}")
            continue
        expected_artifact_paths.add(relative_path)
        artifact_path = repo_root / relative_path
        _check_file_shape(document, artifact_path, errors)
        if artifact_path.is_file():
            if artifact.get("sha256") != _sha256(artifact_path):
                errors.append(f"document artifact hash mismatch: {relative_path}")
            if artifact.get("size_bytes") != artifact_path.stat().st_size:
                errors.append(f"document artifact size mismatch: {relative_path}")
        for section in document.get("sections", []):
            section_id = section.get("section_id")
            key = (document_id, section_id)
            if key in section_by_key:
                errors.append(f"duplicate section locator: {key}")
            section_by_key[key] = section
            if section.get("page_number") != 1 or not isinstance(
                section.get("region"), dict
            ):
                errors.append(f"section must bind page 1 and a region: {key}")

    documents_dir = repo_root / "datasets" / "generated" / "documents"
    actual_artifact_paths = {
        path.relative_to(repo_root).as_posix()
        for path in documents_dir.iterdir()
        if path.is_file()
    }
    if actual_artifact_paths != expected_artifact_paths:
        errors.append(
            "document directory differs from manifest: "
            f"missing={sorted(expected_artifact_paths - actual_artifact_paths)}, "
            f"extra={sorted(actual_artifact_paths - expected_artifact_paths)}"
        )

    fact_ids: set[str] = set()
    evidence_ids: set[str] = set()
    fact_by_evidence: dict[str, dict[str, Any]] = {}
    for fact in facts:
        fact_id = fact.get("fact_id")
        evidence_id = fact.get("evidence_id")
        if not isinstance(fact_id, str) or not re.fullmatch(r"syn-fact-\d{4}", fact_id):
            errors.append(f"invalid fact_id: {fact_id!r}")
        if not isinstance(evidence_id, str) or not re.fullmatch(
            r"syn-ev-\d{4}", evidence_id
        ):
            errors.append(f"invalid evidence_id: {evidence_id!r}")
            continue
        if fact_id in fact_ids:
            errors.append(f"duplicate fact_id: {fact_id}")
        if evidence_id in evidence_ids:
            errors.append(f"duplicate evidence_id: {evidence_id}")
        fact_ids.add(fact_id)
        evidence_ids.add(evidence_id)
        fact_by_evidence[evidence_id] = fact
        if fact.get("truth_status") != "canonical_synthetic":
            errors.append(f"fact {fact_id} is not canonical_synthetic")
        locator = fact.get("locator", {})
        key = (locator.get("document_id"), locator.get("section_id"))
        document = document_by_id.get(locator.get("document_id"))
        section = section_by_key.get(key)
        if document is None or document.get("role") != "primary":
            errors.append(f"fact {fact_id} must bind a primary document: {key}")
        if section is None:
            errors.append(f"fact {fact_id} binds an unknown section: {key}")
        elif locator.get("page_number") != section.get("page_number") or locator.get(
            "region"
        ) != section.get("region"):
            errors.append(f"fact {fact_id} locator differs from section locator")

    if corpus.get("canonical_facts", {}).get("sha256") != _sha256(facts_path):
        errors.append("canonical facts hash differs from corpus manifest")

    if len(cases) != EXPECTED_COUNTS["cases"]:
        errors.append(f"case count is {len(cases)}, expected 40")
    case_ids = [case.get("case_id") for case in cases]
    if len(case_ids) != len(set(case_ids)):
        errors.append("case_id values must be unique")
    if case_ids != [f"syn-q{index:03d}" for index in range(1, 41)]:
        errors.append(
            "case_id values must be the complete stable sequence syn-q001..syn-q040"
        )

    answerable_count = sum(case.get("answerable") is True for case in cases)
    unanswerable_count = sum(case.get("answerable") is False for case in cases)
    split_counts = Counter(case.get("split") for case in cases)
    required_evidence_count = 0
    used_evidence: list[str] = []
    group_splits: dict[str, set[str]] = defaultdict(set)
    group_sizes: Counter[str] = Counter()
    for case in cases:
        case_id = case.get("case_id")
        if case.get("dataset_label") != "VERIFIED_SYNTHETIC":
            errors.append(f"case {case_id} must be labeled VERIFIED_SYNTHETIC")
        if not str(case.get("question", "")).startswith("[合成题]"):
            errors.append(
                f"case {case_id} question must explicitly say it is synthetic"
            )
        split = case.get("split")
        group = case.get("scenario_group")
        group_splits[group].add(split)
        group_sizes[group] += 1
        gold = case.get("gold", {})
        required = gold.get("required_evidence", [])
        if case.get("answerable") is True:
            if (
                not required
                or gold.get("expected_disposition") != "answer"
                or not gold.get("expected_answer")
            ):
                errors.append(f"answerable case {case_id} has incomplete gold")
        else:
            if (
                required
                or gold.get("expected_answer") is not None
                or gold.get("expected_disposition") != "insufficient_evidence"
            ):
                errors.append(
                    f"unanswerable case {case_id} must have empty evidence and insufficient_evidence disposition"
                )
        required_evidence_count += len(required)
        for gold_evidence in required:
            evidence_id = gold_evidence.get("evidence_id")
            used_evidence.append(evidence_id)
            fact = fact_by_evidence.get(evidence_id)
            if fact is None:
                errors.append(
                    f"case {case_id} references unknown evidence {evidence_id}"
                )
                continue
            expected = {
                "evidence_id": fact["evidence_id"],
                "fact_id": fact["fact_id"],
                **fact["locator"],
            }
            if gold_evidence != expected:
                errors.append(
                    f"case {case_id} evidence {evidence_id} does not exactly match canonical locator"
                )

    observed_case_counts = {
        "cases": len(cases),
        "answerable": answerable_count,
        "unanswerable": unanswerable_count,
        "dev": split_counts["dev"],
        "test": split_counts["test"],
        "required_evidence": required_evidence_count,
    }
    for key, observed in observed_case_counts.items():
        if observed != EXPECTED_COUNTS[key]:
            errors.append(f"{key} is {observed}, expected {EXPECTED_COUNTS[key]}")
        if freeze.get("counts", {}).get(key) != EXPECTED_COUNTS[key]:
            errors.append(
                f"freeze manifest frozen count {key} is not {EXPECTED_COUNTS[key]}"
            )
    if Counter(used_evidence) != Counter(evidence_ids):
        errors.append(
            "the 40 required-evidence entries must use every canonical evidence item exactly once"
        )
    if len(group_splits) != 10 or any(
        len(splits) != 1 for splits in group_splits.values()
    ):
        errors.append("scenario groups must be split-stable and total exactly 10")
    if any(size != 4 for size in group_sizes.values()):
        errors.append("every scenario group must contain exactly four cases")

    dev_cases = [case for case in cases if case.get("split") == "dev"]
    test_cases = [case for case in cases if case.get("split") == "test"]
    closest_cross_split = {"ratio": 0.0, "dev_case": None, "test_case": None}
    for dev_case in dev_cases:
        dev_question = _normalized_question(dev_case["question"])
        for test_case in test_cases:
            test_question = _normalized_question(test_case["question"])
            ratio = difflib.SequenceMatcher(None, dev_question, test_question).ratio()
            if ratio > closest_cross_split["ratio"]:
                closest_cross_split = {
                    "ratio": round(ratio, 6),
                    "dev_case": dev_case["case_id"],
                    "test_case": test_case["case_id"],
                }
            if ratio >= 0.82:
                errors.append(
                    f"near-duplicate questions cross split: {dev_case['case_id']} and {test_case['case_id']} ratio={ratio:.3f}"
                )

    freeze_entries = freeze.get("files", [])
    frozen_paths: set[str] = set()
    for entry in freeze_entries:
        relative_path = entry.get("path")
        if not isinstance(relative_path, str):
            errors.append("freeze entry is missing path")
            continue
        if relative_path in frozen_paths:
            errors.append(f"duplicate freeze path: {relative_path}")
        frozen_paths.add(relative_path)
        if (
            relative_path.startswith(("tmp/", ".tmp/"))
            or "/tmp/" in relative_path
            or "/.tmp/" in relative_path
        ):
            errors.append(
                f"forbidden temporary path in freeze manifest: {relative_path}"
            )
            continue
        path = repo_root / relative_path
        if not path.is_file():
            errors.append(f"frozen file is missing: {relative_path}")
            continue
        if entry.get("sha256") != _sha256(path):
            errors.append(f"frozen file hash mismatch: {relative_path}")
        if entry.get("size_bytes") != path.stat().st_size:
            errors.append(f"frozen file size mismatch: {relative_path}")
    expected_frozen_paths = expected_artifact_paths | {
        "datasets/generated/canonical_facts.jsonl",
        "datasets/generated/corpus_manifest.json",
        "evaluation/frozen_cases.jsonl",
    }
    if frozen_paths != expected_frozen_paths:
        errors.append(
            "freeze path set is incomplete or contains extras: "
            f"missing={sorted(expected_frozen_paths - frozen_paths)}, extra={sorted(frozen_paths - expected_frozen_paths)}"
        )
    generator = freeze.get("generator", {})
    generator_path = repo_root / str(generator.get("path", ""))
    if not generator_path.is_file():
        errors.append(f"frozen generator is missing: {generator_path}")
    elif generator.get("sha256") != _sha256(generator_path):
        errors.append(
            "generator hash differs from freeze manifest; regenerate fixtures"
        )

    if errors:
        raise DatasetValidationError(errors)
    return {
        "status": "valid",
        "dataset_label": "VERIFIED_SYNTHETIC",
        "corpus_version": corpus["corpus_version"],
        "counts": {**observed_document_counts, **observed_case_counts},
        "scenario_groups": dict(sorted(group_sizes.items())),
        "closest_cross_split_pair": closest_cross_split,
        "freeze_files": len(freeze_entries),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="CVRAG repository root",
    )
    args = parser.parse_args()
    try:
        summary = validate_repository(args.repo_root)
    except DatasetValidationError as exc:
        print(
            json.dumps(
                {"status": "invalid", "errors": exc.errors},
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(1) from exc
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
