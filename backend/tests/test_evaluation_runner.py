from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ingestion import ParserService
from app.models import (
    RetrievalResponse,
    RetrievalVariant,
    ScoreTrace,
    SearchHit,
    SourceLocator,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = REPOSITORY_ROOT / "backend" / "scripts" / "run_evaluation.py"
SPEC = importlib.util.spec_from_file_location("cvrag_run_evaluation", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def _hit(
    filename: str,
    section: dict[str, object],
    page_size: tuple[float, float] | None,
) -> SearchHit:
    region = section["region"]
    assert isinstance(region, dict)
    bbox = None
    if page_size is not None:
        normalized = region["page_bbox"]
        assert isinstance(normalized, list)
        bbox = (
            normalized[0] * page_size[0],
            normalized[1] * page_size[1],
            normalized[2] * page_size[0],
            normalized[3] * page_size[1],
        )
    parser_locator = {
        key: region[key] for key in ("paragraph_index", "line_start", "line_end") if key in region
    }
    chunk_id = f"chunk-{section['section_id']}"
    locator = SourceLocator(
        document_id="runtime-content-hash",
        filename=filename,
        page_number=section["page_number"],
        bbox=bbox,
        table_row=region.get("table_row"),
        parser_locator=parser_locator,
        chunk_id=chunk_id,
    )
    return SearchHit(
        chunk_id=chunk_id,
        text="synthetic",
        source=locator,
        rank=1,
        score=1.0,
        scores=ScoreTrace(rerank=1.0),
    )


def test_all_40_frozen_evidence_ids_have_one_exact_runtime_locator() -> None:
    manifest = json.loads(
        (REPOSITORY_ROOT / "datasets/generated/corpus_manifest.json").read_text(encoding="utf-8")
    )
    resolver = RUNNER.LocatorResolver(manifest)
    resolved: list[str] = []

    for document in manifest["documents"]:
        if document["role"] != "primary":
            continue
        filename = document["filename"]
        for section in document["sections"]:
            page_size = resolver.page_sizes.get((filename, section["page_number"]))
            hit = _hit(filename, section, page_size)
            evidence_ids = resolver.evidence_ids(hit)
            assert evidence_ids == section["evidence_ids"]
            resolved.extend(evidence_ids)

    assert len(resolved) == 40
    assert len(set(resolved)) == 40


def test_primary_locator_ambiguity_fails_closed() -> None:
    manifest = json.loads(
        (REPOSITORY_ROOT / "datasets/generated/corpus_manifest.json").read_text(encoding="utf-8")
    )
    resolver = RUNNER.LocatorResolver(manifest)
    document = next(
        item
        for item in manifest["documents"]
        if item["role"] == "primary" and item["format"] == "pdf"
    )
    width, height = resolver.page_sizes[(document["filename"], 1)]
    locator = SourceLocator(
        document_id="runtime-content-hash",
        filename=document["filename"],
        page_number=1,
        bbox=(0.0, 0.0, width, height),
        chunk_id="ambiguous",
    )
    hit = SearchHit(
        chunk_id="ambiguous",
        text="ambiguous",
        source=locator,
        rank=1,
        score=1.0,
        scores=ScoreTrace(),
    )

    with pytest.raises(RUNNER.LocatorResolutionError):
        resolver.evidence_ids(hit)


def test_real_plain_parsers_cover_every_non_deepdoc_frozen_evidence() -> None:
    manifest = json.loads(
        (REPOSITORY_ROOT / "datasets/generated/corpus_manifest.json").read_text(encoding="utf-8")
    )
    resolver = RUNNER.LocatorResolver(manifest)
    deepdoc_filenames = {
        "synthetic_esd_scan.pdf",
        "synthetic_cable_scan.pdf",
        "synthetic_priority_table.pdf",
        "synthetic_immunity_table.pdf",
    }
    expected: set[str] = set()
    resolved: set[str] = set()

    for document in manifest["documents"]:
        if document["role"] != "primary" or document["filename"] in deepdoc_filenames:
            continue
        expected.update(
            evidence_id
            for section in document["sections"]
            for evidence_id in section["evidence_ids"]
        )
        artifact = REPOSITORY_ROOT / document["artifact"]["path"]
        parsed = ParserService.parse(document["filename"], artifact.read_bytes())
        for rank, section in enumerate(parsed.sections, start=1):
            chunk_id = f"actual-{document['filename']}-{rank}"
            locator = SourceLocator(
                document_id="runtime-content-hash",
                filename=document["filename"],
                page_number=section.page_number,
                section_path=list(section.section_path),
                bbox=section.bbox,
                table_row=section.table_row,
                parser_locator=section.metadata,
                chunk_id=chunk_id,
            )
            hit = SearchHit(
                chunk_id=chunk_id,
                text=section.text,
                source=locator,
                rank=rank,
                score=1.0,
                scores=ScoreTrace(),
            )
            resolved.update(
                evidence_id
                for evidence_id in resolver.evidence_ids(hit)
                if evidence_id.startswith("syn-ev-")
            )

    assert len(expected) == 24
    assert resolved == expected


def test_rerank_degradation_cannot_emit_verified_synthetic(monkeypatch, tmp_path) -> None:
    class Provider:
        evidence_status = "IMPLEMENTED_FAKE_VERIFIED"

        def close(self) -> None:
            return None

    class Client:
        def close(self) -> None:
            return None

    class Index:
        client = Client()

        def rebuild(self) -> None:
            return None

        def all_candidates(self) -> list[object]:
            return []

    class RetrievalService:
        def __init__(self, index, provider) -> None:
            del index, provider

        def search(self, request) -> RetrievalResponse:
            degraded = request.variant in {
                RetrievalVariant.HYBRID_RRF_RERANK,
                RetrievalVariant.ROUTED_HYBRID_RRF_RERANK,
            }
            return RetrievalResponse(
                query=request.query,
                variant=request.variant,
                route="semantic",
                hits=[],
                degraded=degraded,
                degradation_codes=["rerank_unavailable"] if degraded else [],
                latency_ms=1.0,
            )

    cases = [
        {
            "case_id": "case-1",
            "split": "dev",
            "question": "synthetic question",
            "answerable": True,
            "gold": {"required_evidence": []},
        }
    ]
    monkeypatch.setattr(RUNNER, "validate_repository", lambda _: {"status": "valid"})
    monkeypatch.setattr(RUNNER, "_corpus_hash", lambda: "corpus-hash")
    monkeypatch.setattr(RUNNER, "_read_json", lambda _: {"documents": []})
    monkeypatch.setattr(
        RUNNER,
        "load_jsonl",
        lambda path: [] if path.name == "canonical_facts.jsonl" else cases,
    )
    monkeypatch.setattr(RUNNER, "build_provider", lambda _: Provider())
    monkeypatch.setattr(RUNNER, "build_elasticsearch_index", lambda _: Index())
    monkeypatch.setattr(RUNNER, "RetrievalService", RetrievalService)

    report = RUNNER.run(
        SimpleNamespace(
            provider="fake",
            model_dir=tmp_path,
            elasticsearch_url="http://127.0.0.1:9200",
        )
    )

    assert report["status"] == "failed"
    assert report["evidence_status"] == "NOT_VERIFIED"
    assert "variants" not in report["metrics"]
