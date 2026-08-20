from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from app.chunking import CHUNKER_VERSION, build_chunks
from app.config import Settings
from app.db import DocumentRepository
from app.ingestion.errors import IngestionError
from app.ingestion.service import ParserService
from app.models import (
    ChatRequest,
    CitationSource,
    DocumentRecord,
    DocumentStatus,
    IngestResponse,
    RetrievalRequest,
    RetrievalResponse,
    RetrievalVariant,
)
from app.pipeline import pipeline_index_version
from app.providers import ModelProvider, ProviderError
from app.schemas.ingestion import ParsedDocument
from app.search import ElasticsearchIndex, RetrievalService, SearchIndexError


@dataclass(frozen=True)
class IngestionLimits:
    """Post-parse resource limits for a single trusted ingestion workflow."""

    max_parsed_characters: int = 1_000_000
    max_parsed_sections: int = 5_000
    max_document_chunks: int = 5_000


PUBLIC_INGESTION_LIMITS = IngestionLimits()


class ServiceError(RuntimeError):
    def __init__(self, code: str, safe_message: str):
        super().__init__(code)
        self.code = code
        self.safe_message = safe_message


class DocumentService:
    def __init__(
        self,
        settings: Settings,
        repository: DocumentRepository,
        index: ElasticsearchIndex,
        provider: ModelProvider,
        parser: ParserService | None = None,
        limits: IngestionLimits = PUBLIC_INGESTION_LIMITS,
    ):
        self.settings = settings
        self.repository = repository
        self.index = index
        self.provider = provider
        self.parser = parser or ParserService()
        self.limits = limits

    def list_documents(self) -> list[DocumentRecord]:
        return self.repository.list()

    def ingest(self, filename: str, content: bytes) -> IngestResponse:
        return self._ingest(filename, content)

    def ingest_preparsed(
        self,
        filename: str,
        content: bytes,
        parsed: ParsedDocument,
    ) -> IngestResponse:
        """Index a trusted local parser result without exposing a new HTTP capability.

        The public API only calls :meth:`ingest`; private batch tooling uses this
        method after bounded local page-segment parsing.
        """

        if parsed.filename != filename:
            raise ValueError("preparsed filename must match ingestion filename")
        return self._ingest(filename, content, parsed=parsed)

    def _ingest(
        self,
        filename: str,
        content: bytes,
        *,
        parsed: ParsedDocument | None = None,
    ) -> IngestResponse:
        digest = hashlib.sha256(content).hexdigest()
        document_id = f"doc_{digest[:24]}"
        index_version = pipeline_index_version(self.settings)
        existing = self.repository.get(document_id)
        current_pipeline_matches = (
            existing is not None
            and existing.status == DocumentStatus.READY
            and existing.chunker_version == CHUNKER_VERSION
            and existing.index_version == index_version
        )
        if current_pipeline_matches and existing is not None:
            try:
                if self.index.count_document(document_id) == existing.chunk_count:
                    return IngestResponse(document=existing, idempotent=True)
            except SearchIndexError as error:
                raise ServiceError(
                    error.code, "The document index could not be verified."
                ) from error

        parser_version = existing.parser_version if existing else "pending"
        self.repository.upsert(
            document_id=document_id,
            filename=filename,
            sha256=digest,
            parser_version=parser_version,
            chunker_version=CHUNKER_VERSION,
            index_version=index_version,
            status=DocumentStatus.INDEXING,
        )
        try:
            parsed = parsed or self.parser.parse(filename, content, self.settings.CVRAG_MODEL_DIR)
            parser_version = parsed.parser_version
            if (
                len(parsed.sections) > self.limits.max_parsed_sections
                or sum(len(section.text) for section in parsed.sections)
                > self.limits.max_parsed_characters
            ):
                raise ServiceError(
                    "resource_limit_exceeded",
                    "Parsed document exceeds the section or character limit.",
                )
            chunks = build_chunks(document_id, filename, parsed.sections)
            if not chunks:
                raise ServiceError("empty_document", "No indexable text was parsed.")
            if len(chunks) > self.limits.max_document_chunks:
                raise ServiceError("resource_limit_exceeded", "Document exceeds the chunk limit.")
            vectors = self.provider.embed([chunk.text for chunk in chunks])
            if len(vectors) != len(chunks):
                raise ServiceError(
                    "embedding_count_mismatch", "The embedding response was incomplete."
                )
            embedded_chunks = [
                chunk.model_copy(update={"embedding": vector})
                for chunk, vector in zip(chunks, vectors, strict=True)
            ]
            index_warnings = self.index.replace_document(document_id, embedded_chunks)
            record = self.repository.upsert(
                document_id=document_id,
                filename=filename,
                sha256=digest,
                parser_version=parser_version,
                chunker_version=CHUNKER_VERSION,
                index_version=index_version,
                status=DocumentStatus.READY,
                chunk_count=len(embedded_chunks),
            )
            warnings = [f"{warning.code}:{warning.message}" for warning in parsed.warnings]
            warnings.extend(index_warnings)
            return IngestResponse(document=record, idempotent=False, warnings=warnings)
        except IngestionError as error:
            self._restore_or_mark_failed(
                existing,
                document_id,
                filename,
                digest,
                parser_version,
                index_version,
                error.code.value,
            )
            raise ServiceError(error.code.value, error.safe_message) from error
        except (ProviderError, SearchIndexError, ServiceError) as error:
            code = error.code
            self._restore_or_mark_failed(
                existing,
                document_id,
                filename,
                digest,
                parser_version,
                index_version,
                code,
            )
            if isinstance(error, ServiceError):
                raise
            raise ServiceError(code, "Document indexing could not be completed.") from error
        except Exception as error:
            self._restore_or_mark_failed(
                existing,
                document_id,
                filename,
                digest,
                parser_version,
                index_version,
                "internal_ingestion_error",
            )
            raise ServiceError(
                "internal_ingestion_error", "Document indexing could not be completed."
            ) from error

    def _restore_or_mark_failed(
        self,
        existing: DocumentRecord | None,
        document_id: str,
        filename: str,
        digest: str,
        parser_version: str,
        index_version: str,
        code: str,
    ) -> None:
        if existing is not None and existing.status == DocumentStatus.READY:
            self.repository.upsert(
                document_id=existing.document_id,
                filename=existing.filename,
                sha256=existing.sha256,
                parser_version=existing.parser_version,
                chunker_version=existing.chunker_version,
                index_version=existing.index_version,
                status=DocumentStatus.READY,
                chunk_count=existing.chunk_count,
            )
            return
        self._mark_failed(
            document_id,
            filename,
            digest,
            parser_version,
            index_version,
            code,
        )

    def _mark_failed(
        self,
        document_id: str,
        filename: str,
        digest: str,
        parser_version: str,
        index_version: str,
        code: str,
    ) -> None:
        self.repository.upsert(
            document_id=document_id,
            filename=filename,
            sha256=digest,
            parser_version=parser_version,
            chunker_version=CHUNKER_VERSION,
            index_version=index_version,
            status=DocumentStatus.FAILED,
            error_code=code,
        )


@dataclass(frozen=True)
class ChatOutcome:
    status: Literal["answered", "insufficient_evidence", "needs_review"]
    text: str
    sources: list[CitationSource]
    error_code: str | None = None
    retrieval: RetrievalResponse | None = None


class ChatService:
    def __init__(
        self,
        retrieval: RetrievalService,
        provider: ModelProvider,
        refusal_threshold: float,
    ):
        self.retrieval = retrieval
        self.provider = provider
        self.refusal_threshold = refusal_threshold

    def answer(self, request: ChatRequest) -> ChatOutcome:
        retrieval = self.retrieval.search(
            RetrievalRequest(
                query=request.query,
                top_k=5,
                variant=RetrievalVariant.ROUTED_HYBRID_RRF_RERANK,
            )
        )
        if retrieval.degraded:
            return ChatOutcome(
                status="needs_review",
                text="",
                sources=[],
                error_code="retrieval_degraded",
                retrieval=retrieval,
            )
        if not retrieval.hits or retrieval.hits[0].score < self.refusal_threshold:
            return ChatOutcome(
                status="insufficient_evidence",
                text="现有证据不足，无法给出可验证的结论。",
                sources=[],
                retrieval=retrieval,
            )

        sources = [
            CitationSource(citation_id=f"S{index}", hit=hit)
            for index, hit in enumerate(retrieval.hits, start=1)
        ]
        try:
            generated = self.provider.generate(request.query, sources)
        except ProviderError as error:
            return ChatOutcome(
                status="needs_review",
                text="",
                sources=[],
                error_code=error.code,
                retrieval=retrieval,
            )
        if not generated.answerable:
            return ChatOutcome(
                status="insufficient_evidence",
                text="现有证据不足，无法给出可验证的结论。",
                sources=[],
                retrieval=retrieval,
            )

        allowed_citations = {source.citation_id for source in sources}
        for claim in generated.claims:
            if not claim.citation_ids or any(
                citation_id not in allowed_citations for citation_id in claim.citation_ids
            ):
                return ChatOutcome(
                    status="needs_review",
                    text="",
                    sources=[],
                    error_code="invalid_citation",
                    retrieval=retrieval,
                )
        text = "\n".join(
            f"{claim.text} {' '.join(f'【{citation_id}】' for citation_id in claim.citation_ids)}"
            for claim in generated.claims
        )
        return ChatOutcome(status="answered", text=text, sources=sources, retrieval=retrieval)


def build_elasticsearch_index(settings: Settings) -> ElasticsearchIndex:
    from elasticsearch import Elasticsearch

    client = Elasticsearch(settings.ELASTICSEARCH_URL, request_timeout=30)
    return ElasticsearchIndex(
        client=client,
        index_name=settings.CVRAG_INDEX_NAME,
        embedding_dimension=settings.EMBEDDING_DIMENSION,
        index_version=pipeline_index_version(settings),
    )
