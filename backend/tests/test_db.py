from __future__ import annotations

from app.db import DocumentRepository
from app.models import DocumentStatus


def test_document_repository_preserves_created_at_on_status_transition(tmp_path) -> None:
    repository = DocumentRepository(tmp_path / "cvrag.db")
    repository.initialize()
    first = repository.upsert(
        document_id="doc-1",
        filename="sample.txt",
        sha256="abc",
        parser_version="v1",
        chunker_version="v1",
        index_version="v1",
        status=DocumentStatus.INDEXING,
    )

    second = repository.upsert(
        document_id="doc-1",
        filename="sample.txt",
        sha256="abc",
        parser_version="v1",
        chunker_version="v1",
        index_version="v1",
        status=DocumentStatus.READY,
        chunk_count=3,
    )

    assert second.created_at == first.created_at
    assert second.status == DocumentStatus.READY
    assert second.chunk_count == 3
