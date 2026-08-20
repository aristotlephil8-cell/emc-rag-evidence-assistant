from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Protocol

import tiktoken

from app.models import Chunk, SourceLocator

CHUNKER_VERSION = "structured-token-v1"
MAX_CHUNK_TOKENS = 256


class ParsedSectionLike(Protocol):
    text: str
    page_number: int | None
    section_path: tuple[str, ...] | list[str]
    bbox: tuple[float, float, float, float] | None
    table_row: int | None
    metadata: dict[str, object]


def _token_windows(text: str, maximum: int = MAX_CHUNK_TOKENS) -> Iterable[str]:
    encoding = tiktoken.get_encoding("cl100k_base")
    token_ids = encoding.encode(text)
    if not token_ids:
        return
    for start in range(0, len(token_ids), maximum):
        yield encoding.decode(token_ids[start : start + maximum]).strip()


def build_chunks(
    document_id: str, filename: str, sections: Iterable[ParsedSectionLike]
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section_index, section in enumerate(sections):
        for window_index, text in enumerate(_token_windows(section.text)):
            if not text:
                continue
            text_hash = hashlib.sha256(text.encode()).hexdigest()
            identity = f"{document_id}:{section_index}:{window_index}:{text_hash}"
            chunk_id = hashlib.sha256(identity.encode()).hexdigest()[:32]
            source = SourceLocator(
                document_id=document_id,
                filename=filename,
                page_number=section.page_number,
                section_path=list(section.section_path),
                bbox=section.bbox,
                table_index=(
                    section.metadata.get("table_index")
                    if isinstance(section.metadata.get("table_index"), int)
                    else None
                ),
                table_row=section.table_row,
                parser_locator=dict(section.metadata),
                chunk_id=chunk_id,
            )
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    filename=filename,
                    text=text,
                    source=source,
                )
            )
    return chunks
