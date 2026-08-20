from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.config import Settings, get_settings
from app.db import DocumentRepository
from app.models import (
    ChatRequest,
    DocumentRecord,
    EvaluationReport,
    IngestResponse,
    RetrievalRequest,
    RetrievalResponse,
)
from app.pipeline import locked_refusal_threshold
from app.providers import ModelProvider, ProviderError, build_provider
from app.search import ElasticsearchIndex, QueryRouter, RetrievalService, SearchIndexError
from app.services import (
    ChatOutcome,
    ChatService,
    DocumentService,
    ServiceError,
    build_elasticsearch_index,
)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
INGESTION_HTTP_STATUS = {
    "unsupported_file_type": 422,
    "file_signature_mismatch": 422,
    "unsupported_text_encoding": 422,
    "document_empty": 422,
    "document_corrupted": 422,
    "document_encrypted": 422,
    "parse_failed": 422,
    "resource_limit_exceeded": 413,
    "parse_timeout": 408,
}


@lru_cache
def get_repository() -> DocumentRepository:
    settings = get_settings()
    repository = DocumentRepository(settings.database_path)
    repository.initialize()
    return repository


@lru_cache
def get_provider() -> ModelProvider:
    return build_provider(get_settings())


@lru_cache
def get_index() -> ElasticsearchIndex:
    return build_elasticsearch_index(get_settings())


@lru_cache
def get_retrieval_service() -> RetrievalService:
    return RetrievalService(get_index(), get_provider())


@lru_cache
def get_document_service() -> DocumentService:
    return DocumentService(get_settings(), get_repository(), get_index(), get_provider())


@lru_cache
def get_chat_service() -> ChatService:
    settings = get_settings()
    return ChatService(
        get_retrieval_service(),
        get_provider(),
        locked_refusal_threshold(settings),
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_repository().initialize()
    provider = get_provider()
    index = get_index()
    if get_settings().CVRAG_PROVIDER == "dashscope":
        get_chat_service()
    try:
        yield
    finally:
        provider.close()
        index.client.close()


app = FastAPI(
    title="EMC_RAG",
    version="0.1.0",
    description="Evidence-grounded EMC retrieval and citation demo",
    lifespan=lifespan,
)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def health(
    index: Annotated[ElasticsearchIndex, Depends(get_index)],
    provider: Annotated[ModelProvider, Depends(get_provider)],
):
    try:
        elasticsearch_ok = bool(index.client.ping())
    except Exception:
        elasticsearch_ok = False
    return {
        "status": "ok" if elasticsearch_ok else "degraded",
        "elasticsearch": elasticsearch_ok,
        "provider_evidence_status": provider.evidence_status,
    }


@app.post("/api/v1/documents/ingest", response_model=IngestResponse)
async def ingest_document(
    file: Annotated[UploadFile, File()],
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> IngestResponse:
    filename = Path(file.filename or "").name
    if not filename:
        raise HTTPException(status_code=422, detail={"code": "filename_missing"})
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail={"code": "file_too_large"})
    try:
        return await run_in_threadpool(service.ingest, filename, content)
    except ServiceError as error:
        status_code = INGESTION_HTTP_STATUS.get(error.code, 503)
        raise HTTPException(
            status_code=status_code,
            detail={"code": error.code, "message": error.safe_message},
        ) from error


@app.get("/api/v1/documents", response_model=list[DocumentRecord])
def list_documents(
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> list[DocumentRecord]:
    return service.list_documents()


@app.post("/api/v1/retrieval/search", response_model=RetrievalResponse)
def retrieval_search(
    request: RetrievalRequest,
    service: Annotated[RetrievalService, Depends(get_retrieval_service)],
) -> RetrievalResponse:
    try:
        return service.search(request)
    except (ProviderError, SearchIndexError) as error:
        raise HTTPException(status_code=503, detail={"code": error.code}) from error
    except Exception as error:
        raise HTTPException(status_code=503, detail={"code": "retrieval_unavailable"}) from error


def _event(name: str, payload: dict[str, object]) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _text_chunks(text: str, size: int = 24) -> Iterator[str]:
    for start in range(0, len(text), size):
        yield text[start : start + size]


def _retrieval_trace(outcome: ChatOutcome) -> dict[str, object] | None:
    retrieval = outcome.retrieval
    if retrieval is None:
        return None
    lexical_weight, vector_weight = QueryRouter.weights(retrieval.route)
    return {
        "route": {
            "name": retrieval.route,
            "lexical_weight": lexical_weight,
            "vector_weight": vector_weight,
        },
        "latency_ms": retrieval.latency_ms,
        "rerank_applied": not retrieval.degraded,
        "degraded": retrieval.degraded,
        "degradation_codes": retrieval.degradation_codes,
        "candidates": [
            {
                "chunk_id": hit.chunk_id,
                "filename": hit.source.filename,
                "bm25_score": hit.scores.bm25,
                "vector_score": hit.scores.vector,
                "rrf_score": hit.scores.rrf,
                "rerank_score": hit.scores.rerank,
                "final_rank": hit.rank,
            }
            for hit in retrieval.hits
        ],
    }


def _stream_outcome(outcome: ChatOutcome, evidence_status: str) -> Iterator[str]:
    trace = _retrieval_trace(outcome)
    if outcome.status == "needs_review":
        yield _event(
            "error",
            {
                "code": "needs_review",
                "reason": outcome.error_code or "validation_failed",
                "message": "引用、检索或结构校验未通过，需要人工复核。",
            },
        )
        done_payload: dict[str, object] = {
            "status": outcome.status,
            "evidence_status": evidence_status,
        }
        if trace is not None:
            done_payload["trace"] = trace
        yield _event("done", done_payload)
        return
    if outcome.sources:
        yield _event(
            "sources",
            {
                "sources": [source.model_dump(mode="json") for source in outcome.sources],
                "trace": trace,
            },
        )
    for token in _text_chunks(outcome.text):
        yield _event("token", {"text": token})
    done_payload = {"status": outcome.status, "evidence_status": evidence_status}
    if trace is not None:
        done_payload["trace"] = trace
    yield _event("done", done_payload)


@app.post("/api/v1/chat/stream")
def chat_stream(
    request: ChatRequest,
    service: Annotated[ChatService, Depends(get_chat_service)],
    provider: Annotated[ModelProvider, Depends(get_provider)],
) -> StreamingResponse:
    try:
        outcome = service.answer(request)
    except (ProviderError, SearchIndexError) as error:
        outcome = ChatOutcome(status="needs_review", text="", sources=[], error_code=error.code)
    except Exception:
        outcome = ChatOutcome(
            status="needs_review",
            text="",
            sources=[],
            error_code="chat_unavailable",
        )
    return StreamingResponse(
        _stream_outcome(outcome, provider.evidence_status),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/evaluation/latest", response_model=EvaluationReport)
def latest_evaluation(
    current_settings: Annotated[Settings, Depends(get_settings)],
) -> EvaluationReport:
    report_path = current_settings.CVRAG_EVALUATION_REPORT
    if not report_path.exists():
        return EvaluationReport(
            status="not_run",
            evidence_status="NOT_VERIFIED",
            limitations=["No reproducible evaluation report has been generated."],
        )
    try:
        content = report_path.read_text(encoding="utf-8")
        return EvaluationReport.model_validate_json(content)
    except (OSError, ValidationError, ValueError):
        return EvaluationReport(
            status="invalid_report",
            evidence_status="NOT_VERIFIED",
            limitations=["The evaluation artifact failed schema validation."],
        )
