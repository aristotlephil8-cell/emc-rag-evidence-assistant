from __future__ import annotations

import pytest

from app.models import RetrievalRequest, RetrievalVariant, SourceLocator
from app.providers import FakeProvider, ProviderError
from app.search import (
    Candidate,
    QueryRouter,
    RetrievalService,
    weighted_rrf,
)


def candidate(chunk_id: str, score: float, text: str | None = None) -> Candidate:
    return Candidate(
        chunk_id=chunk_id,
        text=text or f"evidence {chunk_id}",
        source=SourceLocator(
            document_id="doc-1",
            filename="sample.txt",
            page_number=1,
            section_path=["section"],
            chunk_id=chunk_id,
        ),
        score=score,
    )


class StubBackend:
    def __init__(self):
        self.lexical = [candidate("a", 9.0), candidate("b", 8.0)]
        self.semantic = [candidate("b", 0.9), candidate("c", 0.8)]

    def bm25(self, query: str, limit: int = 50) -> list[Candidate]:
        del query, limit
        return self.lexical

    def vector(self, query_vector: list[float], limit: int = 50) -> list[Candidate]:
        del query_vector, limit
        return self.semantic


class FailingRerankProvider(FakeProvider):
    def rerank(self, query: str, documents: list[str]) -> list[float]:
        del query, documents
        raise ProviderError("provider_network_error")


def test_query_router_recognizes_exact_identifiers_and_units() -> None:
    assert QueryRouter.route("IEC 61000-4-4 第5.2条怎么设置？") == "exact"
    assert QueryRouter.route("场强 10 V/m 时如何整改？") == "exact"
    assert QueryRouter.route("为什么屏蔽层接地会影响共模干扰？") == "semantic"


def test_weighted_rrf_is_stable_and_rewards_shared_evidence() -> None:
    lexical = [candidate("a", 10.0), candidate("b", 8.0)]
    semantic = [candidate("b", 1.0), candidate("c", 0.8)]

    fused = weighted_rrf(lexical, semantic, 0.5, 0.5)

    assert [item[0].chunk_id for item in fused] == ["b", "a", "c"]
    assert fused[0][1] == pytest.approx(0.5 / 62 + 0.5 / 61)


def test_routed_retrieval_records_all_score_stages() -> None:
    service = RetrievalService(StubBackend(), FakeProvider(dimension=8))

    response = service.search(
        RetrievalRequest(
            query="IEC 61000-4-4",
            variant=RetrievalVariant.ROUTED_HYBRID_RRF_RERANK,
        )
    )

    assert response.route == "exact"
    assert not response.degraded
    assert response.hits
    assert all(hit.scores.rrf is not None for hit in response.hits)
    assert all(hit.scores.rerank is not None for hit in response.hits)


def test_rerank_failure_degrades_to_rrf_without_hiding_failure() -> None:
    service = RetrievalService(StubBackend(), FailingRerankProvider(dimension=8))

    response = service.search(RetrievalRequest(query="整改建议", top_k=2))

    assert response.degraded
    assert response.degradation_codes == ["rerank_unavailable"]
    assert all(hit.scores.rerank is None for hit in response.hits)


def test_rrf_rejects_ambiguous_weights() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        weighted_rrf([], [], 0.7, 0.7)
