from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from app.ingestion.errors import IngestionError, IngestionErrorCode
from app.ingestion.pdf_plain import PlainPdfResult
from app.models import RetrievalResponse, RetrievalVariant
from app.private_evaluation import (
    blend_rerank_with_rrf,
    private_gates_pass,
    private_metrics,
    private_retrieval_metrics,
    select_private_refusal_threshold,
    select_private_rerank_weight,
)
from app.private_rag import (
    PRIVATE_CASE_PROVENANCE,
    PRIVATE_EVIDENCE_STATUS,
    PrivateEvaluationCase,
    PrivateRagError,
    build_private_weak_cases,
    load_private_cases,
    parse_private_pdf,
    partition_private_parsed,
    prepare_private_pdf,
)
from app.schemas.ingestion import BlockType, FileType, ParsedDocument, ParsedSection
from app.services import ChatOutcome


class _PdfReader:
    def __init__(self, path: Path):
        del path
        self.is_encrypted = False
        self.pages = list(range(55))


class _PdfWriter:
    def __init__(self):
        self.pages: list[int] = []

    def add_page(self, page: int) -> None:
        self.pages.append(page)

    def write(self, output) -> None:
        output.write(b"%PDF-private-test")


@pytest.fixture
def private_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "private_local"
    root.mkdir()
    source = root / "ocr_textbook.pdf"
    source.write_bytes(b"%PDF-private-source")
    monkeypatch.setattr(
        "app.private_rag._pypdf",
        lambda: (_PdfReader, _PdfWriter),
    )
    return root, source, prepare_private_pdf(source, root, segment_pages=25)


def test_prepare_private_pdf_creates_opaque_bounded_segments(private_run) -> None:
    root, source, run = private_run

    assert run.root == root.resolve()
    assert run.page_count == 55
    assert run.dev_page_end == 41
    assert run.holdout_page_start == 42
    assert [(start, end) for start, end, _ in run.segments] == [(1, 25), (26, 50), (51, 55)]
    protocol = json.loads(run.manifest_path.read_text(encoding="utf-8"))
    assert source.name not in run.manifest_path.read_text(encoding="utf-8")
    assert protocol["evidence_status"] == PRIVATE_EVIDENCE_STATUS
    assert protocol["split"] == {
        "strategy": "contiguous_pages",
        "dev_pages": [1, 41],
        "holdout_pages": [42, 55],
    }
    assert all(path.parent == run.run_dir / "segments" for _, _, path in run.segments)


def test_parse_and_partition_rebases_private_segment_page_locators(
    private_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, run = private_run

    def fake_parse(filename: str, content: bytes, model_dir: Path) -> ParsedDocument:
        del content, model_dir
        return ParsedDocument(
            filename=filename,
            file_type=FileType.PDF,
            parser_name="fake",
            parser_version="fake-v1",
            sections=(
                ParsedSection(text="first", block_type=BlockType.PDF_TEXT, page_number=1),
                ParsedSection(text="second", block_type=BlockType.PDF_TEXT, page_number=2),
            ),
        )

    monkeypatch.setattr("app.private_rag.ParserService.parse", fake_parse)
    monkeypatch.setattr(
        "app.private_rag.parse_plain_pdf",
        lambda content: PlainPdfResult((), (), 2, False),
    )
    parsed = parse_private_pdf(run, Path("unused"))

    assert parsed.filename == run.document_filename
    assert [section.page_number for section in parsed.sections] == [1, 2, 26, 27, 51, 52]
    assert [section.metadata["private_segment"] for section in parsed.sections] == [
        1,
        1,
        2,
        2,
        3,
        3,
    ]
    assert [
        section.page_number for section in partition_private_parsed(run, parsed, "dev").sections
    ] == [
        1,
        2,
        26,
        27,
    ]
    assert [
        section.page_number for section in partition_private_parsed(run, parsed, "holdout").sections
    ] == [51, 52]


def test_private_parser_retains_plain_text_when_deepdoc_ocr_is_empty(
    private_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, run = private_run

    def empty_ocr(filename: str, content: bytes, model_dir: Path) -> ParsedDocument:
        del filename, content, model_dir
        raise IngestionError(
            IngestionErrorCode.PARSE_FAILED,
            "DeepDOC OCR produced no usable text",
        )

    def plain_text(content: bytes) -> PlainPdfResult:
        del content
        return PlainPdfResult(
            sections=(
                ParsedSection(text="retained", block_type=BlockType.PDF_TEXT, page_number=1),
            ),
            warnings=(),
            page_count=1,
            requires_deepdoc=False,
        )

    monkeypatch.setattr("app.private_rag.ParserService.parse", empty_ocr)
    monkeypatch.setattr("app.private_rag.parse_plain_pdf", plain_text)

    parsed = parse_private_pdf(run, Path("unused"))

    assert parsed.parser_name == "private_segmented_pdf"
    assert len(parsed.sections) == len(run.segments)
    assert any(
        warning.code == "private_deepdoc_ocr_empty_plain_fallback" for warning in parsed.warnings
    )


def test_private_parser_prioritizes_complete_page_located_text(
    private_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, run = private_run

    def should_not_call_deepdoc(filename: str, content: bytes, model_dir: Path) -> ParsedDocument:
        raise AssertionError((filename, content, model_dir))

    def complete_plain_text(content: bytes) -> PlainPdfResult:
        del content
        return PlainPdfResult(
            sections=(
                ParsedSection(text="retained", block_type=BlockType.PDF_TEXT, page_number=1),
            ),
            warnings=(),
            page_count=1,
            requires_deepdoc=True,
        )

    monkeypatch.setattr("app.private_rag.ParserService.parse", should_not_call_deepdoc)
    monkeypatch.setattr("app.private_rag.parse_plain_pdf", complete_plain_text)

    parsed = parse_private_pdf(run, Path("unused"))

    assert len(parsed.sections) == len(run.segments)
    assert any(
        warning.code == "private_deepdoc_skipped_text_coverage" for warning in parsed.warnings
    )


def test_private_cases_cannot_cross_continuous_split(private_run) -> None:
    root, _, run = private_run
    cases = root / "private_cases.jsonl"
    cases.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "case_id": "dev-001",
                        "stage": "dev",
                        "query": "private dev question",
                        "answerable": True,
                        "evidence_pages": [2],
                    }
                ),
                json.dumps(
                    {
                        "case_id": "holdout-001",
                        "stage": "holdout",
                        "query": "private holdout question",
                        "answerable": False,
                        "evidence_pages": [],
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    dev_cases, case_hash = load_private_cases(cases, root, run, "dev")

    assert [case.case_id for case in dev_cases] == ["dev-001"]
    assert len(case_hash) == 64
    cases.write_text(
        json.dumps(
            {
                "case_id": "dev-leak",
                "stage": "dev",
                "query": "private dev question",
                "answerable": True,
                "evidence_pages": [42],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(PrivateRagError, match="private_case_split_leakage"):
        load_private_cases(cases, root, run, "dev")


def test_private_weak_cases_are_page_anchored_and_frozen_once(private_run) -> None:
    _, _, run = private_run
    sections = tuple(
        ParsedSection(
            text=f"连续页段技术要求示例{page:03d}的适用条件与验证步骤",
            block_type=BlockType.PDF_TEXT,
            page_number=page,
        )
        for page in range(1, 56)
    )
    parsed = ParsedDocument(
        filename=run.document_filename,
        file_type=FileType.PDF,
        parser_name="test",
        parser_version="test-v1",
        sections=sections,
    )

    case_path, manifest = build_private_weak_cases(run, parsed)
    dev_cases, _ = load_private_cases(case_path, run.root, run, "dev")
    holdout_cases, _ = load_private_cases(case_path, run.root, run, "holdout")

    assert manifest["case_provenance"] == PRIVATE_CASE_PROVENANCE
    assert len(dev_cases) == 16
    assert len(holdout_cases) == 12
    assert {case.case_provenance for case in (*dev_cases, *holdout_cases)} == {
        PRIVATE_CASE_PROVENANCE
    }
    assert all(case.evidence_pages for case in dev_cases if case.answerable)
    assert all(not case.evidence_pages for case in holdout_cases if not case.answerable)
    with pytest.raises(PrivateRagError, match="private_weak_cases_already_frozen"):
        build_private_weak_cases(run, parsed)


def _response(case_id: str, *, page_number: int, score: float, degraded: bool = False):
    from app.models import ScoreTrace, SearchHit, SourceLocator

    hit = SearchHit(
        chunk_id=f"chunk-{case_id}",
        text="private evidence",
        source=SourceLocator(
            document_id="doc-private",
            filename="private.pdf",
            page_number=page_number,
            chunk_id=f"chunk-{case_id}",
        ),
        rank=1,
        score=score,
        scores=ScoreTrace(rerank=score),
    )
    return RetrievalResponse(
        query="private query",
        variant=RetrievalVariant.ROUTED_HYBRID_RRF_RERANK,
        route="semantic",
        hits=[hit],
        degraded=degraded,
        degradation_codes=["rerank_unavailable"] if degraded else [],
        latency_ms=4.0,
    )


def test_private_metrics_only_accept_server_validated_citations(private_run) -> None:
    root, _, run = private_run
    cases_path = root / "cases.jsonl"
    cases_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "case_id": "dev-1",
                        "stage": "dev",
                        "query": "question one",
                        "answerable": True,
                        "evidence_pages": [1],
                    }
                ),
                json.dumps(
                    {
                        "case_id": "dev-2",
                        "stage": "dev",
                        "query": "question two",
                        "answerable": False,
                        "evidence_pages": [],
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    cases, _ = load_private_cases(cases_path, root, run, "dev")
    threshold, accuracy = select_private_refusal_threshold(
        cases,
        {"dev-1": 0.9, "dev-2": 0.1},
    )
    retrievals = {
        "dev-1": _response("dev-1", page_number=1, score=0.9),
        "dev-2": _response("dev-2", page_number=2, score=0.1),
    }
    outcomes = {
        "dev-1": ChatOutcome(status="answered", text="", sources=[]),
        "dev-2": ChatOutcome(status="insufficient_evidence", text="", sources=[]),
    }

    metrics = private_metrics(cases, retrievals, outcomes, threshold)

    assert accuracy == 1.0
    assert threshold == 0.9
    assert metrics["evidence_recall_at_5"] == 1.0
    assert metrics["server_citation_id_validity"] == 1.0
    assert private_gates_pass(metrics)


def test_private_retrieval_metrics_calculates_page_grounded_ablations(private_run) -> None:
    root, _, run = private_run
    cases_path = root / "ablation_cases.jsonl"
    cases_path.write_text(
        json.dumps(
            {
                "case_id": "dev-1",
                "stage": "dev",
                "query": "private page question",
                "answerable": True,
                "evidence_pages": [1],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cases, _ = load_private_cases(cases_path, root, run, "dev")

    metrics = private_retrieval_metrics(
        cases,
        {"dev-1": _response("dev-1", page_number=1, score=0.8)},
    )

    assert metrics["evidence_recall_at_5"] == 1.0
    assert metrics["mrr_at_10"] == 1.0
    assert metrics["ndcg_at_5"] == 1.0
    assert metrics["context_precision_at_5"] == 1.0


def test_private_dev_selects_positive_rerank_rrf_blend() -> None:
    from app.models import ScoreTrace, SearchHit, SourceLocator

    case = PrivateEvaluationCase(
        case_id="dev-1",
        stage="dev",
        query="private query",
        answerable=True,
        evidence_pages=(1,),
    )

    def hit(chunk_id: str, page_number: int, rerank: float, rrf: float) -> SearchHit:
        return SearchHit(
            chunk_id=chunk_id,
            text="private evidence",
            source=SourceLocator(
                document_id="doc-private",
                filename="private.pdf",
                page_number=page_number,
                chunk_id=chunk_id,
            ),
            rank=1,
            score=rerank,
            scores=ScoreTrace(rerank=rerank, rrf=rrf),
        )

    bm25 = _response("dev-1", page_number=2, score=0.9)
    reranked = RetrievalResponse(
        query="private query",
        variant=RetrievalVariant.ROUTED_HYBRID_RRF_RERANK,
        route="semantic",
        hits=[
            hit("gold", 1, rerank=0.4, rrf=1 / 61),
            hit("other", 2, rerank=0.9, rrf=1 / 110),
        ],
        latency_ms=4.0,
    )

    weight, selected, metrics = select_private_rerank_weight(
        (case,), {"dev-1": bm25}, {"dev-1": reranked}
    )

    assert 0.0 < weight <= 1.0
    assert selected["dev-1"].hits[0].source.page_number == 1
    assert metrics["evidence_recall_at_5"] == 1.0
    assert blend_rerank_with_rrf(reranked, weight, top_k=1).hits[0].rank == 1


def test_holdout_attempt_marker_is_single_use(tmp_path: Path) -> None:
    script_path = Path(__file__).parents[1] / "scripts" / "private_rag.py"
    spec = importlib.util.spec_from_file_location("private_rag_script_test", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module._mark_holdout_attempt(
        tmp_path,
        source_sha256="a" * 64,
        cases_sha256="b" * 64,
        config_hash="c" * 64,
        case_provenance=PRIVATE_CASE_PROVENANCE,
    )
    with pytest.raises(module.PrivateRagError, match="private_holdout_already_attempted"):
        module._mark_holdout_attempt(
            tmp_path,
            source_sha256="a" * 64,
            cases_sha256="b" * 64,
            config_hash="c" * 64,
            case_provenance=PRIVATE_CASE_PROVENANCE,
        )
