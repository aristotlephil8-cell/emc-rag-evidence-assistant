from __future__ import annotations

import math
from enum import Enum
from pathlib import PurePath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IngestionModel(BaseModel):
    """Strict immutable models shared by parsing and indexing."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class FileType(str, Enum):
    TXT = "txt"
    DOCX = "docx"
    PDF = "pdf"


class BlockType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    PDF_TEXT = "pdf_text"
    OCR_TEXT = "ocr_text"
    IMAGE_CAPTION = "image_caption"


class ParseWarning(IngestionModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    page_number: int | None = Field(default=None, ge=1)


class ParsedSection(IngestionModel):
    """A retrievable structural unit with a source locator.

    PDF bounding boxes use PDF points in a top-left coordinate system. DOCX
    and TXT do not expose stable page coordinates, so their page and bbox are
    ``None`` and the original block/line locator is carried in ``metadata``.
    """

    text: str
    block_type: BlockType
    page_number: int | None = Field(default=None, ge=1)
    section_path: tuple[str, ...] = ()
    bbox: tuple[float, float, float, float] | None = None
    table_row: int | None = Field(default=None, ge=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("section text must contain non-whitespace content")
        return value

    @field_validator("section_path")
    @classmethod
    def validate_section_path(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not part.strip() for part in value):
            raise ValueError("section path components must contain text")
        return value

    @field_validator("bbox")
    @classmethod
    def validate_bbox(
        cls,
        value: tuple[float, float, float, float] | None,
    ) -> tuple[float, float, float, float] | None:
        if value is None:
            return None
        if not all(math.isfinite(coordinate) for coordinate in value):
            raise ValueError("bbox coordinates must be finite")
        x0, y0, x1, y1 = value
        if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
            raise ValueError("bbox must contain positive ordered bounds")
        return value


class ParsedDocument(IngestionModel):
    filename: str
    file_type: FileType
    parser_name: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    sections: tuple[ParsedSection, ...]
    warnings: tuple[ParseWarning, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if not value or PurePath(value).name != value or "/" in value or "\\" in value:
            raise ValueError("filename must be a basename")
        return value

    @field_validator("sections")
    @classmethod
    def validate_sections(
        cls,
        value: tuple[ParsedSection, ...],
    ) -> tuple[ParsedSection, ...]:
        if not value:
            raise ValueError("parsed document must contain at least one section")
        return value


__all__ = [
    "BlockType",
    "FileType",
    "ParsedDocument",
    "ParsedSection",
    "ParseWarning",
]
