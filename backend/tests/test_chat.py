from __future__ import annotations

from app.models import (
    ChatRequest,
    Claim,
    GeneratedAnswer,
    RetrievalResponse,
    RetrievalVariant,
    ScoreTrace,
    SearchHit,
    SourceLocator,
)
from app.providers import OfflineProvider
from app.services import ChatService


def hit(score: float = 0.9) -> SearchHit:
    source = SourceLocator(
        document_id="doc-1",
        filename="sample.txt",
        page_number=2,
        section_path=["整改"],
        chunk_id="chunk-1",
    )
    return SearchHit(
        chunk_id="chunk-1",
        text="证据内容",
        source=source,
        rank=1,
        score=score,
        scores=ScoreTrace(rerank=score),
    )


class StubRetrieval:
    def __init__(self, score: float = 0.9, degraded: bool = False):
        self.score = score
        self.degraded = degraded

    def search(self, request):
        return RetrievalResponse(
            query=request.query,
            variant=RetrievalVariant.ROUTED_HYBRID_RRF_RERANK,
            route="semantic",
            hits=[hit(self.score)],
            degraded=self.degraded,
            degradation_codes=["rerank_unavailable"] if self.degraded else [],
            latency_ms=1.0,
        )


class GeneratedProvider(OfflineProvider):
    def __init__(self, generated: GeneratedAnswer):
        super().__init__(dimension=8)
        self.generated = generated

    def generate(self, query, sources):
        del query, sources
        return self.generated


def test_low_confidence_is_refused_before_generation() -> None:
    generated = GeneratedAnswer(
        answerable=True,
        claims=[Claim(text="不应出现", citation_ids=["S1"])],
        missing_information="",
    )
    service = ChatService(StubRetrieval(score=0.2), GeneratedProvider(generated), 0.45)

    outcome = service.answer(ChatRequest(query="问题"))

    assert outcome.status == "insufficient_evidence"
    assert outcome.sources == []


def test_valid_claims_are_rendered_with_server_owned_citations() -> None:
    generated = GeneratedAnswer(
        answerable=True,
        claims=[Claim(text="整改结论", citation_ids=["S1"])],
        missing_information="",
    )
    service = ChatService(StubRetrieval(), GeneratedProvider(generated), 0.45)

    outcome = service.answer(ChatRequest(query="问题"))

    assert outcome.status == "answered"
    assert outcome.text == "整改结论 【S1】"
    assert [source.citation_id for source in outcome.sources] == ["S1"]


def test_unknown_citation_is_sent_to_review_and_never_streamed_as_answer() -> None:
    generated = GeneratedAnswer(
        answerable=True,
        claims=[Claim(text="无法验证", citation_ids=["S99"])],
        missing_information="",
    )
    service = ChatService(StubRetrieval(), GeneratedProvider(generated), 0.45)

    outcome = service.answer(ChatRequest(query="问题"))

    assert outcome.status == "needs_review"
    assert outcome.text == ""
    assert outcome.sources == []
    assert outcome.error_code == "invalid_citation"


def test_rerank_degradation_fails_closed_before_generation() -> None:
    generated = GeneratedAnswer(
        answerable=True,
        claims=[Claim(text="不应出现", citation_ids=["S1"])],
        missing_information="",
    )
    service = ChatService(StubRetrieval(degraded=True), GeneratedProvider(generated), 0.45)

    outcome = service.answer(ChatRequest(query="问题"))

    assert outcome.status == "needs_review"
    assert outcome.error_code == "retrieval_degraded"
    assert outcome.text == ""
