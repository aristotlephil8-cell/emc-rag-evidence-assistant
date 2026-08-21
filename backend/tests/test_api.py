from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app, get_chat_service, get_document_service, get_provider
from app.models import (
    CitationSource,
    RetrievalResponse,
    RetrievalVariant,
    ScoreTrace,
    SearchHit,
    SourceLocator,
)
from app.services import ChatOutcome, ServiceError


class StubChatService:
    def __init__(self, outcome: ChatOutcome):
        self.outcome = outcome

    def answer(self, request):
        assert request.query
        return self.outcome


class StubProvider:
    evidence_status = "UNASSESSED_RUNTIME"


class FailingDocumentService:
    def __init__(self, code: str):
        self.code = code

    def ingest(self, filename: str, content: bytes):
        del filename, content
        raise ServiceError(self.code, "safe failure")


def source() -> CitationSource:
    locator = SourceLocator(
        document_id="doc-1",
        filename="sample.txt",
        section_path=["整改"],
        parser_locator={"line_start": 4, "line_end": 4},
        chunk_id="chunk-1",
    )
    hit = SearchHit(
        chunk_id="chunk-1",
        text="可验证证据",
        source=locator,
        rank=1,
        score=0.9,
        scores=ScoreTrace(bm25=2.0, vector=0.8, rrf=0.016, rerank=0.9),
    )
    return CitationSource(citation_id="S1", hit=hit)


def _events(response_text: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    for block in response_text.strip().split("\n\n"):
        lines = block.splitlines()
        event_name = lines[0].removeprefix("event: ")
        payload = json.loads(lines[1].removeprefix("data: "))
        events.append((event_name, payload))
    return events


def test_chat_sends_sources_then_tokens_then_done_only_after_validation() -> None:
    citation_source = source()
    retrieval = RetrievalResponse(
        query="IEC 61000-4-4",
        variant=RetrievalVariant.ROUTED_HYBRID_RRF_RERANK,
        route="exact",
        hits=[citation_source.hit],
        latency_ms=12.5,
    )
    outcome = ChatOutcome(
        status="answered",
        text="整改结论 【S1】",
        sources=[citation_source],
        retrieval=retrieval,
    )
    app.dependency_overrides[get_chat_service] = lambda: StubChatService(outcome)
    app.dependency_overrides[get_provider] = lambda: StubProvider()
    try:
        response = TestClient(app).post("/api/v1/chat/stream", json={"query": "如何整改？"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    events = _events(response.text)
    assert [name for name, _ in events] == ["sources", "token", "done"]
    assert events[0][1]["sources"][0]["citation_id"] == "S1"
    assert events[0][1]["trace"]["route"] == {
        "name": "exact",
        "lexical_weight": 0.7,
        "vector_weight": 0.3,
    }
    assert events[-1][1] == {
        "status": "answered",
        "evidence_status": "UNASSESSED_RUNTIME",
        "trace": events[0][1]["trace"],
    }


def test_needs_review_never_streams_unvalidated_answer_or_source() -> None:
    outcome = ChatOutcome(
        status="needs_review",
        text="must-not-stream",
        sources=[source()],
        error_code="invalid_citation",
    )
    app.dependency_overrides[get_chat_service] = lambda: StubChatService(outcome)
    app.dependency_overrides[get_provider] = lambda: StubProvider()
    try:
        response = TestClient(app).post("/api/v1/chat/stream", json={"query": "问题"})
    finally:
        app.dependency_overrides.clear()

    events = _events(response.text)
    assert [name for name, _ in events] == ["error", "done"]
    assert "must-not-stream" not in response.text
    assert "chunk-1" not in response.text


def test_missing_evaluation_report_is_not_mislabeled_as_verified(tmp_path) -> None:
    current_settings = Settings(
        CVRAG_EVALUATION_REPORT=tmp_path / "missing.json",
        CVRAG_PROVIDER="offline",
    )
    app.dependency_overrides[get_settings] = lambda: current_settings
    try:
        response = TestClient(app).get("/api/v1/evaluation/latest")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "not_run"
    assert response.json()["evidence_status"] == "NOT_VERIFIED"


def test_resource_limit_is_a_client_error_not_infrastructure_failure() -> None:
    service = FailingDocumentService("resource_limit_exceeded")
    app.dependency_overrides[get_document_service] = lambda: service
    try:
        response = TestClient(app).post(
            "/api/v1/documents/ingest",
            files={"file": ("large.pdf", b"synthetic", "application/pdf")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "resource_limit_exceeded"
