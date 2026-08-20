from __future__ import annotations

from pathlib import Path, PurePath

from app.ingestion.docx import parse_docx
from app.ingestion.errors import IngestionError, IngestionErrorCode
from app.ingestion.pdf import parse_pdf
from app.ingestion.txt import parse_txt
from app.schemas.ingestion import ParsedDocument


class ParserService:
    """Synchronous public entry point for the three supported input formats."""

    @staticmethod
    def parse(
        filename: str,
        content: bytes,
        model_dir: Path | None = None,
    ) -> ParsedDocument:
        if not isinstance(filename, str) or not filename:
            raise ValueError("filename must be a non-empty string")
        if PurePath(filename).name != filename or "/" in filename or "\\" in filename:
            raise ValueError("filename must be a basename")
        if not isinstance(content, bytes):
            raise TypeError("content must be bytes")
        if model_dir is not None and not isinstance(model_dir, Path):
            raise TypeError("model_dir must be pathlib.Path or None")

        suffix = Path(filename).suffix.lower()
        if suffix == ".txt":
            return parse_txt(filename, content)
        if suffix == ".docx":
            return parse_docx(filename, content)
        if suffix == ".pdf":
            return parse_pdf(filename, content, model_dir)
        raise IngestionError(
            IngestionErrorCode.UNSUPPORTED_FILE_TYPE,
            "supported document types are PDF, DOCX, and UTF-8 TXT",
        )


__all__ = ["ParserService"]
