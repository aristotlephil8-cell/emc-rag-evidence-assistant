from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.models import DocumentRecord, DocumentStatus


class DocumentRepository:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    parser_version TEXT NOT NULL,
                    chunker_version TEXT NOT NULL,
                    index_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error_code TEXT
                )
                """
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_sha ON documents(sha256)"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def get(self, document_id: str) -> DocumentRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        return self._record(row) if row else None

    def list(self) -> list[DocumentRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM documents ORDER BY updated_at DESC, document_id"
            ).fetchall()
        return [self._record(row) for row in rows]

    def upsert(
        self,
        *,
        document_id: str,
        filename: str,
        sha256: str,
        parser_version: str,
        chunker_version: str,
        index_version: str,
        status: DocumentStatus,
        chunk_count: int = 0,
        error_code: str | None = None,
    ) -> DocumentRecord:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO documents (
                    document_id, filename, sha256, parser_version, chunker_version,
                    index_version, status, chunk_count, created_at, updated_at, error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    filename=excluded.filename,
                    parser_version=excluded.parser_version,
                    chunker_version=excluded.chunker_version,
                    index_version=excluded.index_version,
                    status=excluded.status,
                    chunk_count=excluded.chunk_count,
                    updated_at=excluded.updated_at,
                    error_code=excluded.error_code
                """,
                (
                    document_id,
                    filename,
                    sha256,
                    parser_version,
                    chunker_version,
                    index_version,
                    status.value,
                    chunk_count,
                    now,
                    now,
                    error_code,
                ),
            )
        record = self.get(document_id)
        if record is None:
            raise RuntimeError("document upsert did not persist")
        return record

    @staticmethod
    def _record(row: sqlite3.Row) -> DocumentRecord:
        return DocumentRecord(
            document_id=row["document_id"],
            filename=row["filename"],
            sha256=row["sha256"],
            parser_version=row["parser_version"],
            chunker_version=row["chunker_version"],
            index_version=row["index_version"],
            status=DocumentStatus(row["status"]),
            chunk_count=row["chunk_count"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            error_code=row["error_code"],
        )
