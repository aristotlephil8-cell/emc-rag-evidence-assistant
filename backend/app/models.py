from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class SourceLocator(BaseModel):
    document_id: str
    filename: str
    page_number: int | None = None
    section_path: list[str] = Field(default_factory=list)
    bbox: tuple[float, float, float, float] | None = None
    table_row: int | None = None
    parser_locator: dict[str, Any] = Field(default_factory=dict)
    chunk_id: str


class Chunk(BaseModel):
    chunk_id: str
    document_id: str
    filename: str
    text: str
    source: SourceLocator
    embedding: list[float] | None = None


class DocumentStatus(StrEnum):
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


class DocumentRecord(BaseModel):
    document_id: str
    filename: str
    sha256: str
    parser_version: str
    chunker_version: str
    index_version: str
    status: DocumentStatus
    chunk_count: int = 0
    created_at: datetime
    updated_at: datetime
    error_code: str | None = None


class IngestResponse(BaseModel):
    document: DocumentRecord
    idempotent: bool
    warnings: list[str] = Field(default_factory=list)


class RetrievalVariant(StrEnum):
    BM25 = "bm25"
    VECTOR = "vector"
    HYBRID_RRF = "hybrid_rrf"
    HYBRID_RRF_RERANK = "hybrid_rrf_rerank"
    ROUTED_HYBRID_RRF_RERANK = "routed_hybrid_rrf_rerank"


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)
    variant: RetrievalVariant = RetrievalVariant.ROUTED_HYBRID_RRF_RERANK


class ScoreTrace(BaseModel):
    bm25: float | None = None
    vector: float | None = None
    rrf: float | None = None
    rerank: float | None = None


class SearchHit(BaseModel):
    chunk_id: str
    text: str
    source: SourceLocator
    rank: int
    score: float
    scores: ScoreTrace


class RetrievalResponse(BaseModel):
    query: str
    variant: RetrievalVariant
    route: Literal["exact", "semantic"]
    hits: list[SearchHit]
    degraded: bool = False
    degradation_codes: list[str] = Field(default_factory=list)
    latency_ms: float


class Claim(BaseModel):
    text: str = Field(min_length=1)
    citation_ids: list[str] = Field(min_length=1)


class GeneratedAnswer(BaseModel):
    answerable: bool
    claims: list[Claim] = Field(default_factory=list)
    missing_information: str | None = None

    @model_validator(mode="after")
    def validate_answer_shape(self) -> GeneratedAnswer:
        if self.answerable and not self.claims:
            raise ValueError("answerable response requires claims")
        if not self.answerable and self.claims:
            raise ValueError("unanswerable response cannot contain claims")
        return self


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)


class CitationSource(BaseModel):
    citation_id: str
    hit: SearchHit


class EvaluationReport(BaseModel):
    status: str
    evidence_status: str
    generated_at: str | None = None
    corpus_hash: str | None = None
    config_hash: str | None = None
    runtime_config: dict[str, Any] | None = None
    dataset_validation: dict[str, Any] | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    badcases: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
