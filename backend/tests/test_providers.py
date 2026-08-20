from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.models import CitationSource, ScoreTrace, SearchHit, SourceLocator
from app.providers import DashScopeProvider, FakeProvider, ProviderError


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "DASHSCOPE_API_KEY": "test-only-key",
        "EMBEDDING_DIMENSION": 4,
    }
    values.update(overrides)
    return Settings(**values)


def source(citation_id: str = "S1") -> CitationSource:
    hit = SearchHit(
        chunk_id="chunk-1",
        text="C1 test evidence",
        source=SourceLocator(
            document_id="doc-1",
            filename="synthetic.txt",
            chunk_id="chunk-1",
        ),
        rank=1,
        score=0.9,
        scores=ScoreTrace(rerank=0.9),
    )
    return CitationSource(citation_id=citation_id, hit=hit)


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


def test_dashscope_classifies_client_error_without_retaining_body_text() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            400,
            json={
                "error": {
                    "code": "InvalidParameter",
                    "message": "response_format json_schema rejected",
                }
            },
        )
    )
    provider = DashScopeProvider(settings(), httpx.Client(transport=transport))

    with pytest.raises(ProviderError) as raised:
        provider.embed(["a"])

    assert raised.value.code == "provider_http_400"
    assert raised.value.diagnostic_category == "json_schema_rejected"
    assert "response_format" not in str(raised.value)


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


def test_dashscope_generation_uses_strict_schema_and_retries_one_invalid_shape() -> None:
    requests: list[dict[str, object]] = []
    contents = iter(
        [
            '{"answerable":true,"claims":[{"text":"claim","citation_ids":["S1"]}]}',
            (
                '{"answerable":true,"claims":[{"text":"claim",'
                '"citation_ids":["S1"]}],"missing_information":""}'
            ),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": next(contents)}}]},
        )

    provider = DashScopeProvider(settings(), httpx.Client(transport=httpx.MockTransport(handler)))

    generated = provider.generate("What is C1?", [source()])

    assert generated.claims[0].citation_ids == ["S1"]
    assert len(requests) == 2
    first = requests[0]
    response_format = first["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "json_schema"
    schema = response_format["json_schema"]
    assert isinstance(schema, dict)
    assert schema["strict"] is True
    claim_properties = schema["schema"]["properties"]["claims"]["items"]["properties"]
    assert claim_properties["citation_ids"]["items"]["enum"] == ["S1"]
    assert first["enable_thinking"] is False
    retry_message = requests[1]["messages"][0]
    assert isinstance(retry_message, dict)
    assert "prior response failed" in retry_message["content"]


def test_dashscope_generation_rejects_non_offered_citation_after_one_safe_retry() -> None:
    attempts = 0
    invalid = (
        '{"answerable":true,"claims":[{"text":"claim",'
        '"citation_ids":["S99"]}],"missing_information":""}'
    )

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": invalid}}]})

    provider = DashScopeProvider(settings(), httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(ProviderError) as raised:
        provider.generate("What is C1?", [source("S1")])

    assert raised.value.code == "generation_invalid_structure"
    assert raised.value.diagnostic_category == "citation_outside_allowed_set"
    assert raised.value.retry_attempted is True
    assert attempts == 2


def test_dashscope_generation_classifies_missing_required_field_without_body_text() -> None:
    invalid = '{"answerable":true,"claims":[{"text":"claim","citation_ids":["S1"]}]}'
    provider = DashScopeProvider(
        settings(),
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": invalid}}]},
                )
            )
        ),
    )

    with pytest.raises(ProviderError) as raised:
        provider.generate("What is C1?", [source()])

    assert raised.value.code == "generation_invalid_structure"
    assert raised.value.diagnostic_category == "missing_required_field"
    assert raised.value.retry_attempted is True


def test_dashscope_generation_falls_back_once_when_json_schema_is_rejected() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        response_format = payload["response_format"]
        assert isinstance(response_format, dict)
        if response_format["type"] == "json_schema":
            return httpx.Response(
                400,
                json={"error": {"message": "json_schema is not available"}},
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"answerable":true,"claims":[{"text":"claim",'
                                '"citation_ids":["S1"]}],"missing_information":""}'
                            )
                        }
                    }
                ]
            },
        )

    provider = DashScopeProvider(settings(), httpx.Client(transport=httpx.MockTransport(handler)))

    generated = provider.generate("What is C1?", [source()])

    assert generated.claims[0].citation_ids == ["S1"]
    assert [request["response_format"]["type"] for request in requests] == [
        "json_schema",
        "json_object",
    ]
    assert "prior response failed" in requests[1]["messages"][0]["content"]


def test_generated_answer_rejects_extra_fields_and_duplicate_citations() -> None:
    with pytest.raises(ValueError):
        DashScopeProvider._validate_generated_answer(
            (
                '{"answerable":true,"claims":[{"text":"claim",'
                '"citation_ids":["S1","S1"]}],"missing_information":"","extra":true}'
            ),
            {"S1"},
        )
