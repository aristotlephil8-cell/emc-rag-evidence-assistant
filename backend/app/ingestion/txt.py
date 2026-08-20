from __future__ import annotations

import re

from app.ingestion.errors import IngestionError, IngestionErrorCode
from app.ingestion.versions import TXT_PARSER_VERSION
from app.schemas.ingestion import BlockType, FileType, ParsedDocument, ParsedSection, ParseWarning

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _paragraphs(text: str) -> list[tuple[str, int, int]]:
    result: list[tuple[str, int, int]] = []
    buffered: list[str] = []
    start = 1
    lines = text.split("\n")
    for line_number, line in enumerate(lines, start=1):
        if line.strip():
            if not buffered:
                start = line_number
            buffered.append(line.rstrip())
        elif buffered:
            result.append(("\n".join(buffered), start, line_number - 1))
            buffered = []
    if buffered:
        result.append(("\n".join(buffered), start, len(lines)))
    return result


def parse_txt(filename: str, content: bytes) -> ParsedDocument:
    if not content:
        raise IngestionError(IngestionErrorCode.DOCUMENT_EMPTY, "TXT document is empty")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise IngestionError(
            IngestionErrorCode.UNSUPPORTED_TEXT_ENCODING,
            "TXT document must use UTF-8 encoding",
        ) from exc

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    warnings: list[ParseWarning] = []
    if "\x00" in text:
        nul_count = text.count("\x00")
        text = text.replace("\x00", "")
        warnings.append(
            ParseWarning(
                code="nul_removed",
                message=f"Removed {nul_count} NUL character(s) from TXT input",
            )
        )
    if not text.strip():
        raise IngestionError(
            IngestionErrorCode.DOCUMENT_EMPTY,
            "TXT document contains no usable text",
        )

    sections: list[ParsedSection] = []
    section_path: list[str] = []
    for value, line_start, line_end in _paragraphs(text):
        match = _HEADING.fullmatch(value)
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            section_path[level - 1 :] = [title]
            block_type = BlockType.HEADING
            rendered = title
        else:
            block_type = BlockType.PARAGRAPH
            rendered = value.strip()
        sections.append(
            ParsedSection(
                text=rendered,
                block_type=block_type,
                section_path=tuple(section_path),
                metadata={
                    "line_start": line_start,
                    "line_end": line_end,
                    "coordinate_space": "utf8_text_lines",
                },
            )
        )

    return ParsedDocument(
        filename=filename,
        file_type=FileType.TXT,
        parser_name="utf8_txt",
        parser_version=TXT_PARSER_VERSION,
        sections=tuple(sections),
        warnings=tuple(warnings),
        metadata={"encoding": "utf-8", "section_count": len(sections)},
    )


__all__ = ["TXT_PARSER_VERSION", "parse_txt"]
