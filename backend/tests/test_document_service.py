from __future__ import annotations

import pytest

from app.config import Settings
from app.db import DocumentRepository
from app.models import DocumentStatus
from app.providers import ProviderError
from app.schemas.ingestion import BlockType, FileType, ParsedDocument, ParsedSection
from app.services import DocumentService, ServiceError


class StubParser:
    def parse(self, filename, content, model_dir):
        del content, model_dir
        return ParsedDocument(
            filename=filename,
            file_type=FileType.TXT,
            parser_name="stub",
            parser_version="stub-parser-v1",
            sections=(
                ParsedSection(
                    text="synthetic evidence",
                    block_type=BlockType.PARAGRAPH,
                    metadata={"line_start": 1, "line_end": 1},
                ),
            ),
        )


class StubProvider:
    evidence_status = "IMPLEMENTED_OFFLINE_VERIFIED"

    def __init__(self):
        self.fail = False

    def embed(self, texts):
        if self.fail:
            raise ProviderError("provider_network_error")
        return [[0.0] * 1024 for _ in texts]

    def rerank(self, query, documents):
        del query
        return [1.0] * len(documents)

    def generate(self, query, sources):
        raise AssertionError((query, sources))

    def close(self):
        return None


class StubIndex:
    def __init__(self):
        self.document_counts: dict[str, int] = {}
        self.force_missing = False

    def count_document(self, document_id):
        if self.force_missing:
            return 0
        return self.document_counts.get(document_id, 0)

    def replace_document(self, document_id, chunks):
        self.document_counts[document_id] = len(chunks)
        return []


def test_failed_reingest_restores_previous_ready_record(tmp_path) -> None:
    settings = Settings(CVRAG_DATABASE_URL=f"sqlite:///{tmp_path / 'cvrag.db'}")
    repository = DocumentRepository(settings.database_path)
    repository.initialize()
    index = StubIndex()
    provider = StubProvider()
    service = DocumentService(
        settings,
        repository,
        index,  # type: ignore[arg-type]
        provider,
        StubParser(),  # type: ignore[arg-type]
    )

    first = service.ingest("sample.txt", b"same-public-content")
    index.force_missing = True
    provider.fail = True

    with pytest.raises(ServiceError, match="provider_network_error"):
        service.ingest("sample.txt", b"same-public-content")

    restored = repository.get(first.document.document_id)
    assert restored is not None
    assert restored.status == DocumentStatus.READY
    assert restored.chunk_count == 1
    assert restored.error_code is None


def test_trusted_preparsed_ingestion_reuses_document_chunking_and_indexing(tmp_path) -> None:
    settings = Settings(CVRAG_DATABASE_URL=f"sqlite:///{tmp_path / 'cvrag.db'}")
    repository = DocumentRepository(settings.database_path)
    repository.initialize()
    index = StubIndex()
    provider = StubProvider()
    parsed = StubParser().parse("private.pdf", b"ignored", settings.CVRAG_MODEL_DIR)

    class FailingParser:
        def parse(self, filename, content, model_dir):
            raise AssertionError((filename, content, model_dir))

    service = DocumentService(
        settings,
        repository,
        index,  # type: ignore[arg-type]
        provider,
        FailingParser(),  # type: ignore[arg-type]
    )

    response = service.ingest_preparsed("private.pdf", b"private-content", parsed)

    assert response.document.status == DocumentStatus.READY
    assert response.document.chunk_count == 1
