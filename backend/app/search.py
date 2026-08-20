from __future__ import annotations

import logging
import math
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Literal, Protocol

from elasticsearch import Elasticsearch, helpers

from app.models import (
    Chunk,
    RetrievalRequest,
    RetrievalResponse,
    RetrievalVariant,
    ScoreTrace,
    SearchHit,
    SourceLocator,
)
from app.providers import ModelProvider, ProviderError

RRF_K = 60
INITIAL_LIMIT = 50
RERANK_LIMIT = 30
MAX_EVALUATION_CHUNKS = 10_000
LOGGER = logging.getLogger(__name__)


class SearchIndexError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Candidate:
    chunk_id: str
    text: str
    source: SourceLocator
    score: float


class SearchBackend(Protocol):
    def bm25(self, query: str, limit: int = INITIAL_LIMIT) -> list[Candidate]: ...

    def vector(self, query_vector: list[float], limit: int = INITIAL_LIMIT) -> list[Candidate]: ...


def _response_body(response: object) -> dict[str, object]:
    body = getattr(response, "body", response)
    if not isinstance(body, dict):
        raise SearchIndexError("elasticsearch_invalid_response")
    return body


class ElasticsearchIndex:
    def __init__(
        self,
        client: Elasticsearch,
        index_name: str,
        embedding_dimension: int,
        index_version: str,
    ):
        self.client = client
        self.index_name = index_name
        self.embedding_dimension = embedding_dimension
        self.index_version = index_version
        self._replace_lock = threading.Lock()

    @property
    def mapping(self) -> dict[str, object]:
        return {
            "dynamic": "strict",
            "_meta": {
                "index_version": self.index_version,
                "embedding_dimension": self.embedding_dimension,
            },
            "properties": {
                "chunk_id": {"type": "keyword"},
                "document_id": {"type": "keyword"},
                "filename": {"type": "keyword"},
                "text": {"type": "text"},
                "embedding": {
                    "type": "dense_vector",
                    "dims": self.embedding_dimension,
                    "index": True,
                    "similarity": "cosine",
                },
                "page_number": {"type": "integer"},
                "section_path": {"type": "keyword"},
                "bbox": {"type": "double"},
                "table_row": {"type": "integer"},
                "parser_locator": {"type": "flattened"},
            },
        }

    def _physical_name(self) -> str:
        return f"{self.index_name}-{uuid.uuid4().hex[:12]}"

    def _create_physical_index(self, physical_name: str) -> None:
        self.client.indices.create(
            index=physical_name,
            mappings=self.mapping,
            settings={"index": {"number_of_shards": 1, "number_of_replicas": 0}},
        )

    def _alias_targets(self) -> list[str]:
        if not self.client.indices.exists_alias(name=self.index_name):
            return []
        response = _response_body(self.client.indices.get_alias(name=self.index_name))
        return sorted(response)

    def _validate_mapping(self) -> None:
        response = _response_body(self.client.indices.get_mapping(index=self.index_name))
        if len(response) != 1:
            raise SearchIndexError("index_alias_target_mismatch")
        index_mapping = next(iter(response.values()))
        if not isinstance(index_mapping, dict):
            raise SearchIndexError("index_mapping_missing")
        mappings = index_mapping.get("mappings")
        if not isinstance(mappings, dict):
            raise SearchIndexError("index_mapping_missing")
        metadata = mappings.get("_meta")
        if not isinstance(metadata, dict):
            raise SearchIndexError("index_metadata_missing")
        if (
            metadata.get("index_version") != self.index_version
            or metadata.get("embedding_dimension") != self.embedding_dimension
        ):
            raise SearchIndexError("index_version_mismatch")
        properties = mappings.get("properties")
        if (
            mappings.get("dynamic") != "strict"
            or not isinstance(properties, dict)
            or properties.get("chunk_id", {}).get("type") != "keyword"
            or properties.get("embedding", {}).get("type") != "dense_vector"
            or properties.get("embedding", {}).get("dims") != self.embedding_dimension
        ):
            raise SearchIndexError("index_mapping_mismatch")

    def ensure(self) -> None:
        targets = self._alias_targets()
        if not targets:
            if self.client.indices.exists(index=self.index_name):
                raise SearchIndexError("index_alias_conflict")
            physical_name = self._physical_name()
            self._create_physical_index(physical_name)
            self.client.indices.put_alias(
                index=physical_name, name=self.index_name, is_write_index=True
            )
            targets = [physical_name]
        if len(targets) != 1:
            raise SearchIndexError("index_alias_target_mismatch")
        self._validate_mapping()

    def rebuild(self) -> None:
        with self._replace_lock:
            targets = self._alias_targets()
            if not targets and self.client.indices.exists(index=self.index_name):
                raise SearchIndexError("index_alias_conflict")
            next_target = self._physical_name()
            self._create_physical_index(next_target)
            switched = False
            try:
                if targets:
                    actions: list[dict[str, object]] = [
                        {"remove": {"index": target, "alias": self.index_name}}
                        for target in targets
                    ]
                    actions.append(
                        {
                            "add": {
                                "index": next_target,
                                "alias": self.index_name,
                                "is_write_index": True,
                            }
                        }
                    )
                    self.client.indices.update_aliases(actions=actions)
                else:
                    self.client.indices.put_alias(
                        index=next_target,
                        name=self.index_name,
                        is_write_index=True,
                    )
                switched = True
            finally:
                if not switched and self.client.indices.exists(index=next_target):
                    self.client.indices.delete(index=next_target)
            for target in targets:
                if self.client.indices.exists(index=target):
                    try:
                        self.client.indices.delete(index=target)
                    except Exception:
                        LOGGER.warning(
                            "old Elasticsearch index cleanup deferred",
                            extra={"index": target},
                        )

    def replace_document(self, document_id: str, chunks: list[Chunk]) -> list[str]:
        with self._replace_lock:
            self.ensure()
            current_target = self._alias_targets()[0]
            next_target = self._physical_name()
            self._create_physical_index(next_target)
            switched = False
            try:
                reindex_response = _response_body(
                    self.client.reindex(
                        source={
                            "index": self.index_name,
                            "query": {
                                "bool": {"must_not": [{"term": {"document_id": document_id}}]}
                            },
                        },
                        dest={"index": next_target},
                        wait_for_completion=True,
                        refresh=True,
                        conflicts="proceed",
                    )
                )
                if reindex_response.get("timed_out") or reindex_response.get("failures"):
                    raise SearchIndexError("index_clone_failed")
                actions: list[dict[str, object]] = []
                for chunk in chunks:
                    if chunk.embedding is None or len(chunk.embedding) != self.embedding_dimension:
                        raise SearchIndexError("invalid_chunk_embedding")
                    actions.append(
                        {
                            "_op_type": "index",
                            "_index": next_target,
                            "_id": chunk.chunk_id,
                            "_source": {
                                "chunk_id": chunk.chunk_id,
                                "document_id": chunk.document_id,
                                "filename": chunk.filename,
                                "text": chunk.text,
                                "embedding": chunk.embedding,
                                "page_number": chunk.source.page_number,
                                "section_path": chunk.source.section_path,
                                "bbox": chunk.source.bbox,
                                "table_row": chunk.source.table_row,
                                "parser_locator": chunk.source.parser_locator,
                            },
                        }
                    )
                if actions:
                    helpers.bulk(
                        self.client,
                        actions,
                        refresh="wait_for",
                        raise_on_error=True,
                    )
                indexed_count = _response_body(
                    self.client.count(
                        index=next_target,
                        query={"term": {"document_id": document_id}},
                    )
                ).get("count")
                if indexed_count != len(chunks):
                    raise SearchIndexError("index_document_count_mismatch")
                self.client.indices.update_aliases(
                    actions=[
                        {"remove": {"index": current_target, "alias": self.index_name}},
                        {
                            "add": {
                                "index": next_target,
                                "alias": self.index_name,
                                "is_write_index": True,
                            }
                        },
                    ]
                )
                switched = True
            finally:
                if not switched and self.client.indices.exists(index=next_target):
                    self.client.indices.delete(index=next_target)
            if self.client.indices.exists(index=current_target):
                try:
                    self.client.indices.delete(index=current_target)
                except Exception:
                    LOGGER.warning(
                        "old Elasticsearch index cleanup deferred",
                        extra={"index": current_target},
                    )
                    return ["old_index_cleanup_deferred"]
            return []

    def count_document(self, document_id: str) -> int:
        self.ensure()
        response = _response_body(
            self.client.count(index=self.index_name, query={"term": {"document_id": document_id}})
        )
        count = response.get("count")
        if not isinstance(count, int):
            raise SearchIndexError("elasticsearch_count_invalid")
        return count

    def all_candidates(self, limit: int = MAX_EVALUATION_CHUNKS) -> list[Candidate]:
        self.ensure()
        count_body = _response_body(
            self.client.count(index=self.index_name, query={"match_all": {}})
        )
        total = count_body.get("count")
        if not isinstance(total, int) or total < 0:
            raise SearchIndexError("elasticsearch_count_invalid")
        if total > limit:
            raise SearchIndexError("evaluation_chunk_limit_exceeded")
        if total == 0:
            return []
        response = self.client.search(
            index=self.index_name,
            size=total,
            query={"match_all": {}},
            sort=[{"chunk_id": "asc"}],
            source_excludes=["embedding"],
        )
        return self._candidates(response)

    def _candidates(self, response: object) -> list[Candidate]:
        body = _response_body(response)
        hits_container = body.get("hits")
        if not isinstance(hits_container, dict) or not isinstance(hits_container.get("hits"), list):
            raise SearchIndexError("elasticsearch_hits_missing")
        candidates: list[Candidate] = []
        for hit in hits_container["hits"]:
            if not isinstance(hit, dict) or not isinstance(hit.get("_source"), dict):
                raise SearchIndexError("elasticsearch_hit_invalid")
            source = hit["_source"]
            chunk_id = source.get("chunk_id")
            text = source.get("text")
            document_id = source.get("document_id")
            filename = source.get("filename")
            if not all(isinstance(value, str) for value in [chunk_id, text, document_id, filename]):
                raise SearchIndexError("elasticsearch_source_invalid")
            bbox_value = source.get("bbox")
            bbox = None
            if bbox_value is not None:
                if not isinstance(bbox_value, list | tuple) or len(bbox_value) != 4:
                    raise SearchIndexError("elasticsearch_bbox_invalid")
                bbox = tuple(float(value) for value in bbox_value)
            section_path = source.get("section_path") or []
            if not isinstance(section_path, list) or not all(
                isinstance(value, str) for value in section_path
            ):
                raise SearchIndexError("elasticsearch_section_path_invalid")
            candidates.append(
                Candidate(
                    chunk_id=chunk_id,
                    text=text,
                    source=SourceLocator(
                        document_id=document_id,
                        filename=filename,
                        page_number=source.get("page_number"),
                        section_path=section_path,
                        bbox=bbox,
                        table_row=source.get("table_row"),
                        parser_locator=(
                            source.get("parser_locator")
                            if isinstance(source.get("parser_locator"), dict)
                            else {}
                        ),
                        chunk_id=chunk_id,
                    ),
                    score=float(hit.get("_score") or 0.0),
                )
            )
        return candidates

    def bm25(self, query: str, limit: int = INITIAL_LIMIT) -> list[Candidate]:
        self.ensure()
        response = self.client.search(
            index=self.index_name,
            size=limit,
            query={"match": {"text": {"query": query}}},
            source_excludes=["embedding"],
        )
        return self._candidates(response)

    def vector(self, query_vector: list[float], limit: int = INITIAL_LIMIT) -> list[Candidate]:
        self.ensure()
        if len(query_vector) != self.embedding_dimension:
            raise SearchIndexError("query_embedding_dimension_mismatch")
        response = self.client.search(
            index=self.index_name,
            knn={
                "field": "embedding",
                "query_vector": query_vector,
                "k": limit,
                "num_candidates": max(100, limit),
            },
            source_excludes=["embedding"],
        )
        return self._candidates(response)


class QueryRouter:
    _EXACT_PATTERNS = (
        re.compile(r"\b(?:gb(?:/t)?|iec|cispr|en|iso|mil-std)\s*[-:]?\s*\d", re.I),
        re.compile(r"(?:第\s*)?\d+(?:\.\d+){1,3}\s*(?:条|款|节|章)?"),
        re.compile(r"\b[a-z]{1,8}[-_/]?[a-z0-9]*\d[a-z0-9._/-]*\b", re.I),
        re.compile(
            r"\b\d+(?:\.\d+)?\s*(?:hz|khz|mhz|ghz|v/m|a/m|db(?:u|µ|μ)?v|ns|us|ms)\b",
            re.I,
        ),
    )

    @classmethod
    def route(cls, query: str) -> Literal["exact", "semantic"]:
        exact = any(pattern.search(query) for pattern in cls._EXACT_PATTERNS)
        return "exact" if exact else "semantic"

    @staticmethod
    def weights(route: Literal["exact", "semantic"]) -> tuple[float, float]:
        return (0.7, 0.3) if route == "exact" else (0.3, 0.7)


def weighted_rrf(
    lexical: list[Candidate],
    vector: list[Candidate],
    lexical_weight: float,
    vector_weight: float,
    k: int = RRF_K,
) -> list[tuple[Candidate, float, float | None, float | None]]:
    if not math.isclose(lexical_weight + vector_weight, 1.0, abs_tol=1e-9):
        raise ValueError("RRF weights must sum to 1")
    candidates: dict[str, Candidate] = {}
    lexical_ranks: dict[str, int] = {}
    vector_ranks: dict[str, int] = {}
    for rank, candidate in enumerate(lexical, start=1):
        candidates.setdefault(candidate.chunk_id, candidate)
        lexical_ranks.setdefault(candidate.chunk_id, rank)
    for rank, candidate in enumerate(vector, start=1):
        candidates.setdefault(candidate.chunk_id, candidate)
        vector_ranks.setdefault(candidate.chunk_id, rank)

    fused: list[tuple[Candidate, float, float | None, float | None]] = []
    for chunk_id, candidate in candidates.items():
        lexical_rank = lexical_ranks.get(chunk_id)
        vector_rank = vector_ranks.get(chunk_id)
        score = 0.0
        if lexical_rank is not None:
            score += lexical_weight / (k + lexical_rank)
        if vector_rank is not None:
            score += vector_weight / (k + vector_rank)
        lexical_score = next((item.score for item in lexical if item.chunk_id == chunk_id), None)
        vector_score = next((item.score for item in vector if item.chunk_id == chunk_id), None)
        fused.append((candidate, score, lexical_score, vector_score))
    return sorted(fused, key=lambda item: (-item[1], item[0].chunk_id))


class RetrievalService:
    def __init__(self, backend: SearchBackend, provider: ModelProvider):
        self.backend = backend
        self.provider = provider

    def search(self, request: RetrievalRequest) -> RetrievalResponse:
        started = time.perf_counter()
        route = QueryRouter.route(request.query)
        degradation_codes: list[str] = []

        lexical: list[Candidate] = []
        vector: list[Candidate] = []
        if request.variant != RetrievalVariant.VECTOR:
            lexical = self.backend.bm25(request.query, INITIAL_LIMIT)
        if request.variant != RetrievalVariant.BM25:
            query_vector = self.provider.embed([request.query])[0]
            vector = self.backend.vector(query_vector, INITIAL_LIMIT)

        if request.variant == RetrievalVariant.BM25:
            ranked = [
                (
                    candidate,
                    (max(0.0, candidate.score) / (max(0.0, candidate.score) + 1.0)),
                    ScoreTrace(bm25=candidate.score),
                )
                for candidate in lexical
            ]
        elif request.variant == RetrievalVariant.VECTOR:
            ranked = [
                (
                    candidate,
                    max(0.0, min(1.0, candidate.score)),
                    ScoreTrace(vector=candidate.score),
                )
                for candidate in vector
            ]
        else:
            weights = (
                QueryRouter.weights(route)
                if request.variant == RetrievalVariant.ROUTED_HYBRID_RRF_RERANK
                else (0.5, 0.5)
            )
            fused = weighted_rrf(lexical, vector, *weights)
            max_rrf = 1.0 / (RRF_K + 1)
            ranked = [
                (
                    candidate,
                    min(1.0, rrf_score / max_rrf),
                    ScoreTrace(bm25=bm25_score, vector=vector_score, rrf=rrf_score),
                )
                for candidate, rrf_score, bm25_score, vector_score in fused
            ]

            if (
                request.variant
                in {
                    RetrievalVariant.HYBRID_RRF_RERANK,
                    RetrievalVariant.ROUTED_HYBRID_RRF_RERANK,
                }
                and ranked
            ):
                candidates_to_rerank = ranked[:RERANK_LIMIT]
                try:
                    rerank_scores = self.provider.rerank(
                        request.query, [candidate.text for candidate, _, _ in candidates_to_rerank]
                    )
                    rescored = [
                        (
                            candidate,
                            max(0.0, min(1.0, rerank_score)),
                            trace.model_copy(update={"rerank": rerank_score}),
                        )
                        for (candidate, _, trace), rerank_score in zip(
                            candidates_to_rerank, rerank_scores, strict=True
                        )
                    ]
                    ranked = sorted(
                        rescored,
                        key=lambda item: (
                            -item[1],
                            -(item[2].rrf or 0.0),
                            item[0].chunk_id,
                        ),
                    )
                except ProviderError:
                    degradation_codes.append("rerank_unavailable")

        hits = [
            SearchHit(
                chunk_id=candidate.chunk_id,
                text=candidate.text,
                source=candidate.source,
                rank=rank,
                score=score,
                scores=trace,
            )
            for rank, (candidate, score, trace) in enumerate(ranked[: request.top_k], start=1)
        ]
        return RetrievalResponse(
            query=request.query,
            variant=request.variant,
            route=route,
            hits=hits,
            degraded=bool(degradation_codes),
            degradation_codes=degradation_codes,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
