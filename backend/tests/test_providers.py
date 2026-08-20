from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.providers import DashScopeProvider, FakeProvider, ProviderError


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "DASHSCOPE_API_KEY": "test-only-key",
        "EMBEDDING_DIMENSION": 4,
    }
    values.update(overrides)
    return Settings(**values)


def test_fake_embeddings_are_deterministic_and_normalized() -> None:
    provider = FakeProvider(dimension=16)

    first, second = provider.embed(["IEC 61000", "IEC 61000"])

    assert first == second
    assert sum(value * value for value in first) == pytest.approx(1.0)
    assert provider.evidence_status == "IMPLEMENTED_FAKE_VERIFIED"


def test_dashscope_embedding_restores_provider_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-only-key"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0, 1, 0, 0]},
                    {"index": 0, "embedding": [1, 0, 0, 0]},
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = DashScopeProvider(settings(), client)

    assert provider.embed(["a", "b"]) == [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
    ]


def test_dashscope_retries_transient_status_then_succeeds(monkeypatch) -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(429, json={"message": "retry"})
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 0, 0, 0]}]})

    monkeypatch.setattr("app.providers.time.sleep", lambda _: None)
    provider = DashScopeProvider(settings(), httpx.Client(transport=httpx.MockTransport(handler)))

    assert provider.embed(["a"]) == [[1.0, 0.0, 0.0, 0.0]]
    assert attempts == 3


def test_dashscope_does_not_retry_client_error(monkeypatch) -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(400, json={"message": "invalid"})

    monkeypatch.setattr("app.providers.time.sleep", lambda _: None)
    provider = DashScopeProvider(settings(), httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(ProviderError, match="provider_http_400"):
        provider.embed(["a"])
    assert attempts == 1


def test_dashscope_rejects_embedding_dimension_mismatch() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 0]}]})
    )
    provider = DashScopeProvider(settings(), httpx.Client(transport=transport))

    with pytest.raises(ProviderError, match="embedding_dimension_mismatch"):
        provider.embed(["a"])


def test_dashscope_rejects_non_finite_or_boolean_vector_values() -> None:
    invalid_responses = (
        b'{"data":[{"index":0,"embedding":[1,0,0,NaN]}]}',
        b'{"data":[{"index":0,"embedding":[1,0,0,Infinity]}]}',
        b'{"data":[{"index":0,"embedding":[1,0,0,true]}]}',
    )
    for content in invalid_responses:
        transport = httpx.MockTransport(
            lambda _, body=content: httpx.Response(
                200,
                content=body,
                headers={"Content-Type": "application/json"},
            )
        )
        provider = DashScopeProvider(settings(), httpx.Client(transport=transport))

        with pytest.raises(ProviderError, match="embedding_dimension_mismatch"):
            provider.embed(["a"])


def test_dashscope_does_not_attempt_request_without_key() -> None:
    provider = DashScopeProvider(settings(DASHSCOPE_API_KEY=""))

    with pytest.raises(ProviderError, match="provider_key_missing"):
        provider.embed(["a"])


def test_dashscope_rerank_requires_each_unique_candidate_index() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.8},
                ]
            },
        )
    )
    provider = DashScopeProvider(settings(), httpx.Client(transport=transport))

    with pytest.raises(ProviderError, match="rerank_invalid_index"):
        provider.rerank("query", ["a", "b"])


def test_dashscope_rerank_rejects_non_finite_scores() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            content=b'{"results":[{"index":0,"relevance_score":NaN}]}',
            headers={"Content-Type": "application/json"},
        )
    )
    provider = DashScopeProvider(settings(), httpx.Client(transport=transport))

    with pytest.raises(ProviderError, match="rerank_invalid_index"):
        provider.rerank("query", ["a"])
