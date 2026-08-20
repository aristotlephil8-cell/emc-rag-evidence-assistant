from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Sequence
from typing import Protocol

import httpx

from app.config import Settings
from app.models import CitationSource, Claim, GeneratedAnswer


class ProviderError(RuntimeError):
    """A sanitized model-provider failure safe to expose as an error code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ModelProvider(Protocol):
    evidence_status: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    def rerank(self, query: str, documents: Sequence[str]) -> list[float]: ...

    def generate(self, query: str, sources: Sequence[CitationSource]) -> GeneratedAnswer: ...

    def close(self) -> None: ...


class DashScopeProvider:
    evidence_status = "UNASSESSED_RUNTIME"
    _TRANSIENT_STATUS = {429, 502, 503, 504}

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self._client = client or httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0))

    def _headers(self) -> dict[str, str]:
        if not self.settings.DASHSCOPE_API_KEY:
            raise ProviderError("provider_key_missing")
        return {
            "Authorization": f"Bearer {self.settings.DASHSCOPE_API_KEY}",
            "Content-Type": "application/json",
        }

    def close(self) -> None:
        self._client.close()

    def _post(self, url: str, payload: dict[str, object]) -> dict[str, object]:
        for attempt in range(3):
            try:
                response = self._client.post(url, headers=self._headers(), json=payload)
            except httpx.RequestError as error:
                if attempt < 2:
                    time.sleep(0.2 * (2**attempt))
                    continue
                raise ProviderError("provider_network_error") from error

            if response.status_code in self._TRANSIENT_STATUS and attempt < 2:
                time.sleep(0.2 * (2**attempt))
                continue
            if response.status_code >= 400:
                if response.status_code in {400, 401, 403}:
                    raise ProviderError(f"provider_http_{response.status_code}")
                raise ProviderError("provider_http_error")
            try:
                body = response.json()
            except ValueError as error:
                raise ProviderError("provider_invalid_json") from error
            if not isinstance(body, dict):
                raise ProviderError("provider_invalid_response")
            return body
        raise ProviderError("provider_retry_exhausted")

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 10):
            batch = list(texts[start : start + 10])
            body = self._post(
                f"{self.settings.DASHSCOPE_BASE_URL.rstrip('/')}/embeddings",
                {
                    "model": self.settings.EMBEDDING_MODEL,
                    "input": batch,
                    "dimensions": self.settings.EMBEDDING_DIMENSION,
                    "encoding_format": "float",
                },
            )
            data = body.get("data")
            if not isinstance(data, list) or len(data) != len(batch):
                raise ProviderError("embedding_count_mismatch")
            ordered: list[list[float] | None] = [None] * len(batch)
            for fallback_index, item in enumerate(data):
                if not isinstance(item, dict):
                    raise ProviderError("embedding_invalid_item")
                index = item.get("index", fallback_index)
                embedding = item.get("embedding")
                if not isinstance(index, int) or not 0 <= index < len(batch):
                    raise ProviderError("embedding_invalid_index")
                if (
                    not isinstance(embedding, list)
                    or len(embedding) != self.settings.EMBEDDING_DIMENSION
                    or not all(
                        type(value) in (int, float) and math.isfinite(value) for value in embedding
                    )
                ):
                    raise ProviderError("embedding_dimension_mismatch")
                ordered[index] = [float(value) for value in embedding]
            if any(vector is None for vector in ordered):
                raise ProviderError("embedding_missing_index")
            vectors.extend(vector for vector in ordered if vector is not None)
        return vectors

    def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        if not documents:
            return []
        body = self._post(
            self.settings.DASHSCOPE_RERANK_URL,
            {
                "model": self.settings.RERANK_MODEL,
                "query": query,
                "documents": list(documents),
                "top_n": len(documents),
                "return_documents": False,
            },
        )
        raw_results = body.get("results")
        if raw_results is None and isinstance(body.get("output"), dict):
            raw_results = body["output"].get("results")  # type: ignore[index]
        if not isinstance(raw_results, list) or len(raw_results) != len(documents):
            raise ProviderError("rerank_count_mismatch")
        scores: list[float | None] = [None] * len(documents)
        for item in raw_results:
            if not isinstance(item, dict):
                raise ProviderError("rerank_invalid_item")
            index = item.get("index")
            score = item.get("relevance_score", item.get("score"))
            if (
                not isinstance(index, int)
                or not 0 <= index < len(documents)
                or scores[index] is not None
                or type(score) not in (int, float)
                or not math.isfinite(score)
                or not 0.0 <= score <= 1.0
            ):
                raise ProviderError("rerank_invalid_index")
            scores[index] = float(score)
        if any(score is None for score in scores):
            raise ProviderError("rerank_missing_index")
        return [score for score in scores if score is not None]

    def generate(self, query: str, sources: Sequence[CitationSource]) -> GeneratedAnswer:
        evidence = [
            {
                "citation_id": source.citation_id,
                "text": source.hit.text,
                "locator": source.hit.source.model_dump(mode="json"),
            }
            for source in sources
        ]
        system = (
            "You are an EMC evidence-grounded assistant. Use only the supplied evidence. "
            "Return one JSON object with answerable, claims, and missing_information. "
            "Each factual claim must contain non-empty citation_ids chosen exactly from the "
            "supplied citation_id values. If evidence is insufficient, set answerable=false, "
            "claims=[], and explain the missing information. Do not add Markdown."
        )
        body = self._post(
            f"{self.settings.DASHSCOPE_BASE_URL.rstrip('/')}/chat/completions",
            {
                "model": self.settings.GENERATION_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"query": query, "evidence": evidence}, ensure_ascii=False
                        ),
                    },
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
        )
        try:
            choices = body["choices"]
            content = choices[0]["message"]["content"]  # type: ignore[index]
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderError("generation_invalid_response") from error
        if not isinstance(content, str):
            raise ProviderError("generation_invalid_content")
        try:
            return GeneratedAnswer.model_validate_json(content)
        except ValueError as error:
            raise ProviderError("generation_invalid_structure") from error


def _features(text: str) -> set[str]:
    normalized = text.casefold()
    features = set(re.findall(r"[a-z0-9]+(?:[-./][a-z0-9]+)*", normalized))
    han = re.findall(r"[\u4e00-\u9fff]", normalized)
    features.update(han)
    features.update("".join(han[index : index + 2]) for index in range(len(han) - 1))
    return {feature for feature in features if feature}


class FakeProvider:
    """Deterministic offline provider for contract tests; never effectiveness evidence."""

    evidence_status = "IMPLEMENTED_FAKE_VERIFIED"

    def __init__(self, dimension: int = 1024):
        self.dimension = dimension

    def close(self) -> None:
        return None

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimension
            for feature in sorted(_features(text)):
                digest = hashlib.sha256(feature.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % self.dimension
                sign = 1.0 if digest[4] & 1 else -1.0
                vector[index] += sign
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vectors.append([value / norm for value in vector])
        return vectors

    def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        query_features = _features(query)
        if not query_features:
            return [0.0] * len(documents)
        scores: list[float] = []
        for document in documents:
            document_features = _features(document)
            overlap = len(query_features & document_features) / len(query_features)
            exact_bonus = 0.15 if query.casefold() in document.casefold() else 0.0
            scores.append(min(1.0, overlap + exact_bonus))
        return scores

    def generate(self, query: str, sources: Sequence[CitationSource]) -> GeneratedAnswer:
        del query
        if not sources:
            return GeneratedAnswer(
                answerable=False,
                claims=[],
                missing_information="No evidence was retrieved.",
            )
        first = sources[0]
        claim_text = first.hit.text.strip()[:400]
        if not claim_text:
            return GeneratedAnswer(
                answerable=False,
                claims=[],
                missing_information="Retrieved evidence was empty.",
            )
        return GeneratedAnswer(
            answerable=True,
            claims=[Claim(text=claim_text, citation_ids=[first.citation_id])],
        )


def build_provider(settings: Settings) -> ModelProvider:
    if settings.CVRAG_PROVIDER == "fake":
        return FakeProvider(settings.EMBEDDING_DIMENSION)
    return DashScopeProvider(settings)
