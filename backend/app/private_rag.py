"""Private-only PDF preparation for local CVRAG optimization.

This module deliberately accepts paths only below ``datasets/private_local``.
It never logs source paths, document text, or model responses.  All durable
artifacts use opaque content-hash run directories below that ignored root.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.ingestion import IngestionError, ParserService
from app.ingestion.pdf_plain import PDF_PLAIN_PARSER_VERSION, parse_plain_pdf
from app.schemas.ingestion import FileType, ParsedDocument, ParsedSection, ParseWarning

PRIVATE_EVIDENCE_STATUS = "LOCAL_PRIVATE_ONLINE_EVALUATED"
PRIVATE_PROTOCOL_VERSION = "private-rag-v1"
PRIVATE_MAX_SOURCE_BYTES = 64 * 1024 * 1024
PRIVATE_MAX_SOURCE_PAGES = 1_000
# A 216-DPI A4 scan is roughly 4M pixels. Ten pages stay under DeepDOC's
# 50M-pixel / 160MiB decoded-image guard while remaining below public limits.
PRIVATE_SEGMENT_PAGES = 10
PRIVATE_PLAIN_COVERAGE_THRESHOLD = 0.8
PRIVATE_STAGES = ("dev", "holdout")
PRIVATE_CASE_PROVENANCE = "WEAK_HEURISTIC_PAGE_ANCHORED"
PRIVATE_CASE_FILENAME = "weak_page_anchored_cases.jsonl"
PRIVATE_CASE_MANIFEST_FILENAME = "weak_page_anchored_case_manifest.json"
PRIVATE_DEV_ANSWERABLE_CASES = 12
PRIVATE_DEV_UNANSWERABLE_CASES = 4
PRIVATE_HOLDOUT_ANSWERABLE_CASES = 8
PRIVATE_HOLDOUT_UNANSWERABLE_CASES = 4
_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


class PrivateRagError(RuntimeError):
    """A safe private-workflow error that contains no source identity."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PrivatePdfRun:
    root: Path
    run_dir: Path
    source_sha256: str
    source_bytes: int
    page_count: int
    document_filename: str
    segments: tuple[tuple[int, int, Path], ...]
    segment_pages: int
    dev_page_end: int

    @property
    def holdout_page_start(self) -> int:
        return self.dev_page_end + 1

    @property
    def manifest_path(self) -> Path:
        return self.run_dir / "protocol.json"

    @property
    def parsed_path(self) -> Path:
        return self.run_dir / "parsed_document.json"


@dataclass(frozen=True)
class PrivateEvaluationCase:
    """A private-only case whose text is never returned by CLI status output."""

    case_id: str
    stage: str
    query: str
    answerable: bool
    evidence_pages: tuple[int, ...]
    case_provenance: str = "UNSPECIFIED_PRIVATE_CASE"


def _resolved(path: Path) -> Path:
    try:
        return path.resolve(strict=True)
    except OSError as error:
        raise PrivateRagError("private_path_unavailable") from error


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def private_root(path: Path) -> Path:
    root = _resolved(path)
    if not root.is_dir():
        raise PrivateRagError("private_root_invalid")
    return root


def private_input(path: Path, root: Path) -> Path:
    resolved = _resolved(path)
    if not resolved.is_file() or not _is_within(resolved, root):
        raise PrivateRagError("private_input_outside_root")
    if resolved.suffix.lower() != ".pdf":
        raise PrivateRagError("private_input_not_pdf")
    return resolved


def private_artifact(path: Path, root: Path) -> Path:
    """Resolve any pre-existing private artifact without exposing its identity."""

    resolved = _resolved(path)
    if not resolved.is_file() or not _is_within(resolved, root):
        raise PrivateRagError("private_artifact_outside_root")
    return resolved


def _pypdf() -> tuple[Any, Any]:
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as error:
        raise PrivateRagError("private_pdf_split_dependency_missing") from error
    return PdfReader, PdfWriter


def _source_bytes(path: Path) -> bytes:
    try:
        content = path.read_bytes()
    except OSError as error:
        raise PrivateRagError("private_source_read_failed") from error
    if not content or not content[:1024].lstrip().startswith(b"%PDF-"):
        raise PrivateRagError("private_pdf_signature_invalid")
    if len(content) > PRIVATE_MAX_SOURCE_BYTES:
        raise PrivateRagError("private_source_size_limit")
    return content


def _run_dir(root: Path, source_sha256: str, segment_pages: int) -> Path:
    return root / "runs" / f"sha256-{source_sha256[:16]}-p{segment_pages}"


def _document_filename(source_sha256: str) -> str:
    return f"private_source_{source_sha256[:16]}.pdf"


def _protocol_payload(run: PrivatePdfRun) -> dict[str, object]:
    return {
        "protocol_version": PRIVATE_PROTOCOL_VERSION,
        "evidence_status": PRIVATE_EVIDENCE_STATUS,
        "source_sha256": run.source_sha256,
        "source_bytes": run.source_bytes,
        "page_count": run.page_count,
        "document_filename": run.document_filename,
        "segmentation": {
            "maximum_pages_per_segment": run.segment_pages,
            "segments": [
                {
                    "start_page": start,
                    "end_page": end,
                    "artifact": str(path.relative_to(run.run_dir)).replace("\\", "/"),
                }
                for start, end, path in run.segments
            ],
        },
        "split": {
            "strategy": "contiguous_pages",
            "dev_pages": [1, run.dev_page_end],
            "holdout_pages": [run.holdout_page_start, run.page_count],
        },
    }


def prepare_private_pdf(
    source_path: Path,
    root_path: Path,
    *,
    segment_pages: int = PRIVATE_SEGMENT_PAGES,
    dev_ratio: float = 0.75,
) -> PrivatePdfRun:
    """Split one trusted private PDF into bounded parser segments.

    Segments preserve page order but use opaque generated filenames.  The
    public API and the public parser limits are not changed.
    """

    root = private_root(root_path)
    source = private_input(source_path, root)
    if not 1 <= segment_pages <= 50:
        raise PrivateRagError("private_segment_page_limit_invalid")
    if not 0.5 <= dev_ratio < 1.0:
        raise PrivateRagError("private_dev_ratio_invalid")
    content = _source_bytes(source)
    source_sha256 = hashlib.sha256(content).hexdigest()
    PdfReader, PdfWriter = _pypdf()
    try:
        reader = PdfReader(source)
        if reader.is_encrypted:
            raise PrivateRagError("private_pdf_encrypted")
        page_count = len(reader.pages)
    except PrivateRagError:
        raise
    except Exception as error:
        raise PrivateRagError("private_pdf_split_failed") from error
    if not 1 <= page_count <= PRIVATE_MAX_SOURCE_PAGES:
        raise PrivateRagError("private_source_page_limit")

    run_dir = _run_dir(root, source_sha256, segment_pages)
    segments_dir = run_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    segments: list[tuple[int, int, Path]] = []
    try:
        for ordinal, start in enumerate(range(0, page_count, segment_pages), start=1):
            end = min(start + segment_pages, page_count)
            segment_path = segments_dir / f"segment_{ordinal:04d}.pdf"
            if not segment_path.exists():
                writer = PdfWriter()
                for page in reader.pages[start:end]:
                    writer.add_page(page)
                with segment_path.open("wb") as output:
                    writer.write(output)
            segments.append((start + 1, end, segment_path))
    except OSError as error:
        raise PrivateRagError("private_segment_write_failed") from error

    dev_page_end = max(1, min(page_count - 1, int(page_count * dev_ratio)))
    run = PrivatePdfRun(
        root=root,
        run_dir=run_dir,
        source_sha256=source_sha256,
        source_bytes=len(content),
        page_count=page_count,
        document_filename=_document_filename(source_sha256),
        segments=tuple(segments),
        segment_pages=segment_pages,
        dev_page_end=dev_page_end,
    )
    run.manifest_path.write_text(
        json.dumps(_protocol_payload(run), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return run


def parse_private_pdf(run: PrivatePdfRun, model_dir: Path) -> ParsedDocument:
    """Parse bounded private segments, retaining only opaque local locators."""

    sections: list[ParsedSection] = []
    warnings: list[ParseWarning] = []
    parser_versions: list[str] = []
    for segment_number, (start_page, _, path) in enumerate(run.segments, start=1):
        try:
            content = path.read_bytes()
            plain = parse_plain_pdf(content)
        except (IngestionError, OSError) as error:
            code = getattr(getattr(error, "code", None), "value", "private_parse_failed")
            raise PrivateRagError(code) from error
        located_pages = {section.page_number for section in plain.sections if section.page_number}
        plain_coverage = len(located_pages) / plain.page_count
        if (
            plain.requires_deepdoc
            and plain.sections
            and plain_coverage >= PRIVATE_PLAIN_COVERAGE_THRESHOLD
        ):
            parsed = ParsedDocument(
                filename=path.name,
                file_type=FileType.PDF,
                parser_name="private_plain_text_priority",
                parser_version=f"{PDF_PLAIN_PARSER_VERSION}+private-text-priority-v1",
                sections=plain.sections,
                warnings=plain.warnings
                + (
                    ParseWarning(
                        code="private_deepdoc_skipped_text_coverage",
                        message="Retained complete page-located text before DeepDOC fallback",
                    ),
                ),
                metadata={
                    "route": "private_plain_text_priority",
                    "plain_page_coverage": plain_coverage,
                    "page_count": plain.page_count,
                },
            )
        else:
            try:
                parsed = ParserService.parse(path.name, content, model_dir)
            except IngestionError as error:
                if (
                    error.code.value == "parse_failed"
                    and error.safe_message == "DeepDOC OCR produced no usable text"
                    and plain.sections
                ):
                    parsed = ParsedDocument(
                        filename=path.name,
                        file_type=FileType.PDF,
                        parser_name="private_plain_after_deepdoc_ocr_empty",
                        parser_version=f"{PDF_PLAIN_PARSER_VERSION}+private-fallback-v1",
                        sections=plain.sections,
                        warnings=plain.warnings
                        + (
                            ParseWarning(
                                code="private_deepdoc_ocr_empty_plain_fallback",
                                message=(
                                    "DeepDOC OCR returned no text; retained available plain text"
                                ),
                            ),
                        ),
                        metadata={
                            "route": "private_plain_fallback",
                            "deepdoc_failure": "ocr_empty",
                            "page_count": plain.page_count,
                        },
                    )
                else:
                    code = error.code.value
                    diagnostic = getattr(error, "diagnostic_category", None)
                    if code == "parse_failed" and diagnostic in {
                        "dependency",
                        "memory",
                        "runtime_io",
                        "runtime",
                        "unexpected",
                    }:
                        code = f"private_deepdoc_{diagnostic}"
                    elif code == "parse_failed":
                        code = {
                            "DeepDOC OCR failed": "private_deepdoc_ocr_runtime",
                            "DeepDOC OCR produced no usable text": "private_deepdoc_ocr_empty",
                            "DeepDOC parsing failed": "private_deepdoc_parse_runtime",
                            "DeepDOC worker failed": "private_deepdoc_worker_unexpected",
                        }.get(error.safe_message, "private_parse_failed")
                    raise PrivateRagError(code) from error
            except OSError as error:
                raise PrivateRagError("private_parse_failed") from error
        parser_versions.append(parsed.parser_version)
        for section in parsed.sections:
            page_number = (
                start_page + section.page_number - 1 if section.page_number is not None else None
            )
            metadata = dict(section.metadata)
            metadata["private_segment"] = segment_number
            sections.append(
                section.model_copy(update={"page_number": page_number, "metadata": metadata})
            )
        for warning in parsed.warnings:
            page_number = (
                start_page + warning.page_number - 1 if warning.page_number is not None else None
            )
            warnings.append(warning.model_copy(update={"page_number": page_number}))
    if not sections:
        raise PrivateRagError("private_document_empty")
    document = ParsedDocument(
        filename=run.document_filename,
        file_type=FileType.PDF,
        parser_name="private_segmented_pdf",
        parser_version="private-segmented-v1+" + "+".join(sorted(set(parser_versions))),
        sections=tuple(sections),
        warnings=tuple(warnings),
        metadata={
            "evidence_status": PRIVATE_EVIDENCE_STATUS,
            "page_count": run.page_count,
            "split": {
                "dev_pages": [1, run.dev_page_end],
                "holdout_pages": [run.holdout_page_start, run.page_count],
            },
        },
    )
    run.parsed_path.write_text(
        document.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return document


def load_private_parsed(run: PrivatePdfRun) -> ParsedDocument:
    try:
        return ParsedDocument.model_validate_json(run.parsed_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PrivateRagError("private_parsed_artifact_unavailable") from error


def partition_private_parsed(
    run: PrivatePdfRun,
    parsed: ParsedDocument,
    stage: str,
) -> ParsedDocument:
    """Return an isolated dev or holdout view with mandatory PDF page locators."""

    lower, upper = _stage_bounds(run, stage)
    selected: list[ParsedSection] = []
    for section in parsed.sections:
        if section.page_number is None:
            raise PrivateRagError("private_section_page_locator_missing")
        if lower <= section.page_number <= upper:
            selected.append(section)
    if not selected:
        raise PrivateRagError("private_stage_has_no_sections")
    return parsed.model_copy(
        update={
            "metadata": {
                **parsed.metadata,
                "stage": stage,
                "page_range": [lower, upper],
            },
            "sections": tuple(selected),
        }
    )


def _stage_bounds(run: PrivatePdfRun, stage: str) -> tuple[int, int]:
    if stage not in PRIVATE_STAGES:
        raise PrivateRagError("private_stage_invalid")
    return (1, run.dev_page_end) if stage == "dev" else (run.holdout_page_start, run.page_count)


def _weak_anchor(text: str) -> str | None:
    """Derive a bounded local-only query anchor without emitting source text."""

    normalized = " ".join(text.split())
    for match in re.finditer(r"[\u4e00-\u9fff]{6,}|[A-Za-z][A-Za-z0-9./-]{5,}", normalized):
        candidate = match.group(0).strip("-./")
        if len(candidate) >= 6:
            return candidate[:24]
    return None


def _select_page_anchors(
    run: PrivatePdfRun,
    parsed: ParsedDocument,
    stage: str,
    count: int,
) -> list[tuple[int, str]]:
    lower, upper = _stage_bounds(run, stage)
    by_page: dict[int, str] = {}
    for section in parsed.sections:
        page = section.page_number
        if page is None or page in by_page or not lower <= page <= upper:
            continue
        anchor = _weak_anchor(section.text)
        if anchor is not None:
            by_page[page] = anchor
    candidates = sorted(by_page.items())
    if len(candidates) < count:
        raise PrivateRagError("private_weak_case_anchor_insufficient")
    if count == 1:
        return [candidates[len(candidates) // 2]]
    selected_indexes = {
        round(index * (len(candidates) - 1) / (count - 1)) for index in range(count)
    }
    selected = [
        candidate for index, candidate in enumerate(candidates) if index in selected_indexes
    ]
    if len(selected) != count:
        raise PrivateRagError("private_weak_case_selection_failed")
    return selected


def _private_nonce(run: PrivatePdfRun, stage: str, ordinal: int) -> str:
    seed = f"{run.source_sha256}:{stage}:unanswerable:{ordinal}".encode("ascii")
    return hashlib.sha256(seed).hexdigest()[:16].upper()


def build_private_weak_cases(
    run: PrivatePdfRun,
    parsed: ParsedDocument,
) -> tuple[Path, dict[str, object]]:
    """Freeze a local page-anchored weak-label case pack.

    This intentionally avoids an LLM call before the holdout configuration is
    locked.  Generated queries and anchors are durable only under the ignored
    private root; the manifest has counts and hashes, never source text.
    """

    case_path = run.run_dir / PRIVATE_CASE_FILENAME
    manifest_path = run.run_dir / PRIVATE_CASE_MANIFEST_FILENAME
    if case_path.exists() or manifest_path.exists():
        raise PrivateRagError("private_weak_cases_already_frozen")

    stage_counts = {
        "dev": (PRIVATE_DEV_ANSWERABLE_CASES, PRIVATE_DEV_UNANSWERABLE_CASES),
        "holdout": (PRIVATE_HOLDOUT_ANSWERABLE_CASES, PRIVATE_HOLDOUT_UNANSWERABLE_CASES),
    }
    records: list[dict[str, object]] = []
    for stage, (answerable_count, unanswerable_count) in stage_counts.items():
        for ordinal, (page, anchor) in enumerate(
            _select_page_anchors(run, parsed, stage, answerable_count), start=1
        ):
            records.append(
                {
                    "case_id": f"weak-{stage}-answerable-{ordinal:03d}",
                    "stage": stage,
                    "query": f"请依据资料说明与“{anchor}”相关的技术要求。",
                    "answerable": True,
                    "evidence_pages": [page],
                    "case_provenance": PRIVATE_CASE_PROVENANCE,
                }
            )
        for ordinal in range(1, unanswerable_count + 1):
            nonce = _private_nonce(run, stage, ordinal)
            records.append(
                {
                    "case_id": f"weak-{stage}-unanswerable-{ordinal:03d}",
                    "stage": stage,
                    "query": f"请给出资料中关于 PRIVATE-NONCE-{nonce} 的技术要求。",
                    "answerable": False,
                    "evidence_pages": [],
                    "case_provenance": PRIVATE_CASE_PROVENANCE,
                }
            )

    payload = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    temporary_path = case_path.with_suffix(".jsonl.tmp")
    try:
        temporary_path.write_text(payload, encoding="utf-8", newline="\n")
        os.replace(temporary_path, case_path)
    except OSError as error:
        temporary_path.unlink(missing_ok=True)
        raise PrivateRagError("private_weak_case_write_failed") from error
    manifest = {
        "status": "frozen",
        "evidence_status": PRIVATE_EVIDENCE_STATUS,
        "case_provenance": PRIVATE_CASE_PROVENANCE,
        "cases_sha256": digest,
        "counts": {
            "dev": PRIVATE_DEV_ANSWERABLE_CASES + PRIVATE_DEV_UNANSWERABLE_CASES,
            "holdout": PRIVATE_HOLDOUT_ANSWERABLE_CASES + PRIVATE_HOLDOUT_UNANSWERABLE_CASES,
        },
    }
    try:
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except OSError as error:
        raise PrivateRagError("private_weak_case_manifest_write_failed") from error
    return case_path, manifest


def _case_payload(path: Path) -> tuple[list[dict[str, object]], str]:
    try:
        content = path.read_bytes()
    except OSError as error:
        raise PrivateRagError("private_cases_read_failed") from error
    digest = hashlib.sha256(content).hexdigest()
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise PrivateRagError("private_cases_utf8_required") from error
    raw_cases: list[dict[str, object]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise PrivateRagError("private_cases_json_invalid") from error
        if not isinstance(value, dict):
            raise PrivateRagError("private_cases_record_invalid")
        raw_cases.append(value)
    if not raw_cases:
        raise PrivateRagError("private_cases_empty")
    return raw_cases, digest


def load_private_cases(
    path: Path,
    root: Path,
    run: PrivatePdfRun,
    stage: str,
) -> tuple[tuple[PrivateEvaluationCase, ...], str]:
    """Load only page-grounded cases for a single immutable private stage.

    The JSONL artifact stays below the ignored private root.  It must contain
    ``case_id``, ``stage`` (``dev`` or ``holdout``), ``query``, ``answerable``,
    and ``evidence_pages``.  Answerable cases may only point at pages from
    their own continuous split; this prevents holdout evidence from leaking
    into development threshold selection.
    """

    if stage not in PRIVATE_STAGES:
        raise PrivateRagError("private_stage_invalid")
    case_path = private_artifact(path, root)
    raw_cases, digest = _case_payload(case_path)
    expected_lower, expected_upper = _stage_bounds(run, stage)
    cases: list[PrivateEvaluationCase] = []
    identifiers: set[str] = set()
    for value in raw_cases:
        if value.get("stage") != stage:
            continue
        case_id = value.get("case_id")
        query = value.get("query")
        answerable = value.get("answerable")
        evidence_pages = value.get("evidence_pages")
        case_provenance = value.get("case_provenance", "UNSPECIFIED_PRIVATE_CASE")
        if (
            not isinstance(case_id, str)
            or not _CASE_ID.fullmatch(case_id)
            or case_id in identifiers
            or not isinstance(query, str)
            or not query.strip()
            or len(query) > 1_000
            or type(answerable) is not bool
            or not isinstance(evidence_pages, list)
            or any(type(page) is not int for page in evidence_pages)
            or not isinstance(case_provenance, str)
            or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,79}", case_provenance)
        ):
            raise PrivateRagError("private_case_schema_invalid")
        if len(evidence_pages) != len(set(evidence_pages)):
            raise PrivateRagError("private_case_evidence_duplicate")
        if answerable != bool(evidence_pages):
            raise PrivateRagError("private_case_answerability_invalid")
        if any(not expected_lower <= page <= expected_upper for page in evidence_pages):
            raise PrivateRagError("private_case_split_leakage")
        identifiers.add(case_id)
        cases.append(
            PrivateEvaluationCase(
                case_id=case_id,
                stage=stage,
                query=query.strip(),
                answerable=answerable,
                evidence_pages=tuple(evidence_pages),
                case_provenance=case_provenance,
            )
        )
    if not cases:
        raise PrivateRagError("private_cases_stage_empty")
    return tuple(cases), digest


def private_summary(run: PrivatePdfRun, parsed: ParsedDocument | None = None) -> dict[str, object]:
    """Return a content-free status record suitable for local terminal output."""

    return {
        "status": "ready",
        "evidence_status": PRIVATE_EVIDENCE_STATUS,
        "source_bytes": run.source_bytes,
        "page_count": run.page_count,
        "segment_count": len(run.segments),
        "dev_pages": [1, run.dev_page_end],
        "holdout_pages": [run.holdout_page_start, run.page_count],
        "parsed_sections": len(parsed.sections) if parsed is not None else None,
    }


__all__ = [
    "PRIVATE_EVIDENCE_STATUS",
    "PRIVATE_CASE_FILENAME",
    "PRIVATE_CASE_MANIFEST_FILENAME",
    "PRIVATE_CASE_PROVENANCE",
    "PRIVATE_MAX_SOURCE_BYTES",
    "PRIVATE_MAX_SOURCE_PAGES",
    "PRIVATE_PLAIN_COVERAGE_THRESHOLD",
    "PRIVATE_PROTOCOL_VERSION",
    "PRIVATE_SEGMENT_PAGES",
    "PrivatePdfRun",
    "PrivateEvaluationCase",
    "PrivateRagError",
    "build_private_weak_cases",
    "load_private_parsed",
    "load_private_cases",
    "partition_private_parsed",
    "private_artifact",
    "parse_private_pdf",
    "prepare_private_pdf",
    "private_input",
    "private_root",
    "private_summary",
]
