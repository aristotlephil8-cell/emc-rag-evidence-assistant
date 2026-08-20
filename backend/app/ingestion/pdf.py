from __future__ import annotations

from pathlib import Path

from app.ingestion.errors import IngestionError, IngestionErrorCode
from app.ingestion.pdf_plain import PDF_PLAIN_PARSER_VERSION, parse_plain_pdf
from app.ingestion.pdf_worker import run_deepdoc_worker
from app.schemas.ingestion import FileType, ParsedDocument


def parse_pdf(
    filename: str,
    content: bytes,
    model_dir: Path | None,
) -> ParsedDocument:
    """Extract text first, then route the complete file to DeepDOC if needed."""

    plain = parse_plain_pdf(content)
    if plain.requires_deepdoc:
        if model_dir is None:
            raise IngestionError(
                IngestionErrorCode.MODEL_ASSET_MISSING,
                "DeepDOC model directory is required for scanned or complex PDF input",
            )
        if not model_dir.exists():
            raise IngestionError(
                IngestionErrorCode.MODEL_ASSET_MISSING,
                "DeepDOC model directory is missing",
            )
        parsed = run_deepdoc_worker(filename, content, model_dir)
        return parsed.model_copy(
            update={"warnings": plain.warnings + parsed.warnings},
        )
    if not plain.sections:
        raise IngestionError(
            IngestionErrorCode.DOCUMENT_EMPTY,
            "PDF document contains no usable text",
        )
    return ParsedDocument(
        filename=filename,
        file_type=FileType.PDF,
        parser_name="pypdfium2_plain",
        parser_version=PDF_PLAIN_PARSER_VERSION,
        sections=plain.sections,
        warnings=plain.warnings,
        metadata={
            "route": "plain",
            "page_count": plain.page_count,
            "coordinate_space": "pdf_points_top_left",
        },
    )


__all__ = ["parse_pdf"]
