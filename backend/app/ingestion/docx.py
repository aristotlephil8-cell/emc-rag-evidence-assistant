from __future__ import annotations

import re
import zipfile
from collections.abc import Iterator
from io import BytesIO
from pathlib import PurePosixPath

from docx import Document
from docx.document import Document as DocumentObject
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.ingestion.errors import IngestionError, IngestionErrorCode
from app.ingestion.versions import DOCX_PARSER_VERSION
from app.schemas.ingestion import BlockType, FileType, ParsedDocument, ParsedSection, ParseWarning

_HEADING_LEVEL = re.compile(r"heading\s*([1-9])", re.IGNORECASE)
DOCX_MAX_ENTRIES = 1024
DOCX_MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024


def _validate_docx_package(content: bytes) -> None:
    if not content.startswith(b"PK"):
        raise IngestionError(
            IngestionErrorCode.FILE_SIGNATURE_MISMATCH,
            "stored file is not a DOCX package",
        )
    try:
        with zipfile.ZipFile(BytesIO(content)) as package:
            entries = package.infolist()
            if len(entries) > DOCX_MAX_ENTRIES:
                raise IngestionError(
                    IngestionErrorCode.RESOURCE_LIMIT_EXCEEDED,
                    "DOCX package contains too many entries",
                )
            total_size = 0
            names: set[str] = set()
            for entry in entries:
                normalized_name = entry.filename.replace("\\", "/")
                path = PurePosixPath(normalized_name)
                if path.is_absolute() or ".." in path.parts:
                    raise IngestionError(
                        IngestionErrorCode.DOCUMENT_CORRUPTED,
                        "DOCX package contains an unsafe entry path",
                    )
                if entry.flag_bits & 0x1:
                    raise IngestionError(
                        IngestionErrorCode.DOCUMENT_ENCRYPTED,
                        "DOCX package is encrypted",
                    )
                total_size += entry.file_size
                if total_size > DOCX_MAX_UNCOMPRESSED_BYTES:
                    raise IngestionError(
                        IngestionErrorCode.RESOURCE_LIMIT_EXCEEDED,
                        "DOCX package exceeds the uncompressed size limit",
                    )
                names.add(normalized_name)
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise IngestionError(
                    IngestionErrorCode.FILE_SIGNATURE_MISMATCH,
                    "stored file is not a DOCX document",
                )
    except IngestionError:
        raise
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise IngestionError(
            IngestionErrorCode.DOCUMENT_CORRUPTED,
            "DOCX document is corrupted",
        ) from exc


def _iter_blocks(document: DocumentObject) -> Iterator[Paragraph | Table]:
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, document)
        elif child.tag.endswith("}tbl"):
            yield Table(child, document)


def _escape_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def parse_docx(filename: str, content: bytes) -> ParsedDocument:
    if not content:
        raise IngestionError(IngestionErrorCode.DOCUMENT_EMPTY, "DOCX document is empty")
    _validate_docx_package(content)
    try:
        document = Document(BytesIO(content))
    except Exception as exc:
        raise IngestionError(
            IngestionErrorCode.DOCUMENT_CORRUPTED,
            "DOCX document is corrupted",
        ) from exc

    sections: list[ParsedSection] = []
    warnings: list[ParseWarning] = []
    section_path: list[str] = []
    paragraph_index = 0
    table_index = 0
    block_index = 0

    for block in _iter_blocks(document):
        block_index += 1
        if isinstance(block, Paragraph):
            paragraph_index += 1
            text = block.text.strip()
            if not text:
                continue
            style_name = block.style.name if block.style is not None else ""
            heading = _HEADING_LEVEL.search(style_name)
            if heading:
                level = int(heading.group(1))
                section_path[level - 1 :] = [text]
                block_type = BlockType.HEADING
            else:
                block_type = BlockType.PARAGRAPH
            sections.append(
                ParsedSection(
                    text=text,
                    block_type=block_type,
                    section_path=tuple(section_path),
                    metadata={
                        "block_index": block_index,
                        "paragraph_index": paragraph_index,
                        "style": style_name,
                        "coordinate_space": "docx_body_order",
                    },
                )
            )
            continue

        table_index += 1
        row_count = len(block.rows)
        column_count = max((len(row.cells) for row in block.rows), default=0)
        for row_number, row in enumerate(block.rows, start=1):
            values = [_escape_cell(cell.text.strip()) for cell in row.cells]
            if not any(values):
                continue
            rendered = "| " + " | ".join(values) + " |"
            sections.append(
                ParsedSection(
                    text=rendered,
                    block_type=BlockType.TABLE,
                    section_path=tuple(section_path),
                    table_row=row_number,
                    metadata={
                        "block_index": block_index,
                        "table_index": table_index,
                        "row_count": row_count,
                        "column_count": column_count,
                        "is_header_row": row_number == 1,
                        "coordinate_space": "docx_table_rows",
                    },
                )
            )

    if document.inline_shapes:
        warnings.append(
            ParseWarning(
                code="embedded_image_not_extracted",
                message="DOCX embedded images were not extracted",
            )
        )
    if not sections:
        raise IngestionError(
            IngestionErrorCode.DOCUMENT_EMPTY,
            "DOCX document contains no usable text",
        )
    return ParsedDocument(
        filename=filename,
        file_type=FileType.DOCX,
        parser_name="python_docx",
        parser_version=DOCX_PARSER_VERSION,
        sections=tuple(sections),
        warnings=tuple(warnings),
        metadata={
            "section_count": len(sections),
            "table_count": table_index,
            "paragraph_count": paragraph_index,
        },
    )


__all__ = [
    "DOCX_MAX_ENTRIES",
    "DOCX_MAX_UNCOMPRESSED_BYTES",
    "DOCX_PARSER_VERSION",
    "parse_docx",
]
