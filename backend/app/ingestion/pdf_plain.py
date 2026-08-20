from __future__ import annotations

import re
from ctypes import c_float
from dataclasses import dataclass
from io import BytesIO

import pypdfium2

from app.ingestion.errors import IngestionError, IngestionErrorCode
from app.ingestion.versions import PDF_PLAIN_PARSER_VERSION
from app.schemas.ingestion import BlockType, ParsedSection, ParseWarning

PDF_MAX_BYTES = 20 * 1024 * 1024
PDF_MAX_PAGES = 50
_HEADING = re.compile(r"^(?:\d+(?:\.\d+)*\s+.+|第.{1,20}[章节]\s*.*)$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass(frozen=True)
class PlainPdfResult:
    sections: tuple[ParsedSection, ...]
    warnings: tuple[ParseWarning, ...]
    page_count: int
    requires_deepdoc: bool


@dataclass(frozen=True)
class _TextRegion:
    text: str
    bbox: tuple[float, float, float, float]
    object_count: int
    extraction_source: str


def validate_pdf_preflight(content: bytes) -> tuple[pypdfium2.PdfDocument, int]:
    if not content:
        raise IngestionError(IngestionErrorCode.DOCUMENT_EMPTY, "PDF document is empty")
    if len(content) > PDF_MAX_BYTES:
        raise IngestionError(
            IngestionErrorCode.RESOURCE_LIMIT_EXCEEDED,
            "PDF document exceeds the 20 MiB limit",
        )
    if not content[:1024].lstrip().startswith(b"%PDF-"):
        raise IngestionError(
            IngestionErrorCode.FILE_SIGNATURE_MISMATCH,
            "stored file is not a PDF document",
        )
    try:
        document = pypdfium2.PdfDocument(BytesIO(content))
        page_count = len(document)
    except Exception as exc:
        message = str(exc).lower()
        code = (
            IngestionErrorCode.DOCUMENT_ENCRYPTED
            if "password" in message or "encrypted" in message
            else IngestionErrorCode.DOCUMENT_CORRUPTED
        )
        safe_message = (
            "PDF document is encrypted"
            if code == IngestionErrorCode.DOCUMENT_ENCRYPTED
            else "PDF document is corrupted"
        )
        raise IngestionError(code, safe_message) from exc
    if page_count == 0:
        document.close()
        raise IngestionError(IngestionErrorCode.DOCUMENT_EMPTY, "PDF document has no pages")
    if page_count > PDF_MAX_PAGES:
        document.close()
        raise IngestionError(
            IngestionErrorCode.RESOURCE_LIMIT_EXCEEDED,
            "PDF document exceeds the 50 page limit",
        )
    return document, page_count


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return (
        stripped.count("|") >= 2
        or stripped.count("\t") >= 2
        or len(re.findall(r"\s{3,}", stripped)) >= 2
    )


def _clean_text(value: str) -> tuple[str, int]:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    controls = len(_CONTROL.findall(normalized))
    return _CONTROL.sub("", normalized).replace("\ufffd", "").strip(), controls


def _top_left_bbox(
    bounds: tuple[float, float, float, float],
    *,
    width: float,
    height: float,
) -> tuple[float, float, float, float]:
    left, bottom, right, top = (float(value) for value in bounds)
    epsilon = 1e-6
    x0 = max(0.0, min(left, width - epsilon))
    y0 = max(0.0, min(height - top, height - epsilon))
    x1 = max(x0 + epsilon, min(right, width))
    y1 = max(y0 + epsilon, min(height - bottom, height))
    return (x0, y0, x1, y1)


def _union_bbox(
    regions: list[_TextRegion],
) -> tuple[float, float, float, float]:
    return (
        min(region.bbox[0] for region in regions),
        min(region.bbox[1] for region in regions),
        max(region.bbox[2] for region in regions),
        max(region.bbox[3] for region in regions),
    )


def _merge_text_objects(regions: list[_TextRegion]) -> list[_TextRegion]:
    ordered = sorted(regions, key=lambda region: (region.bbox[1], region.bbox[0], region.text))
    lines: list[list[_TextRegion]] = []
    for region in ordered:
        if not lines:
            lines.append([region])
            continue
        current = lines[-1]
        current_bbox = _union_bbox(current)
        region_height = region.bbox[3] - region.bbox[1]
        current_height = current_bbox[3] - current_bbox[1]
        overlap = max(
            0.0,
            min(region.bbox[3], current_bbox[3]) - max(region.bbox[1], current_bbox[1]),
        )
        center_gap = abs(
            (region.bbox[1] + region.bbox[3]) / 2 - (current_bbox[1] + current_bbox[3]) / 2
        )
        horizontal_gap = max(0.0, region.bbox[0] - current_bbox[2])
        same_baseline = (
            overlap / max(1e-6, min(region_height, current_height)) >= 0.4 or center_gap <= 2.0
        )
        close_horizontally = horizontal_gap <= max(36.0, min(region_height, current_height) * 6)
        if same_baseline and close_horizontally:
            current.append(region)
        else:
            lines.append([region])

    merged: list[_TextRegion] = []
    for line in lines:
        values = sorted(line, key=lambda region: (region.bbox[0], region.bbox[1], region.text))
        text = " ".join(value.text.strip() for value in values if value.text.strip()).strip()
        if not text:
            continue
        merged.append(
            _TextRegion(
                text=text,
                bbox=_union_bbox(values),
                object_count=sum(value.object_count for value in values),
                extraction_source=values[0].extraction_source,
            )
        )
    return merged


def _extract_text_regions(
    page: pypdfium2.PdfPage,
    text_page: pypdfium2.PdfTextPage,
    *,
    width: float,
    height: float,
) -> list[_TextRegion]:
    regions: list[_TextRegion] = []
    for obj in page.get_objects(
        filter=[pypdfium2.raw.FPDF_PAGEOBJ_TEXT],
        textpage=text_page,
    ):
        try:
            text, _ = _clean_text(obj.extract())
            text = " ".join(text.split())
            if not text:
                continue
            regions.append(
                _TextRegion(
                    text=text,
                    bbox=_top_left_bbox(obj.get_bounds(), width=width, height=height),
                    object_count=1,
                    extraction_source="text_object",
                )
            )
        except Exception:
            continue
    if regions:
        return _merge_text_objects(regions)

    try:
        rectangle_count = text_page.count_rects()
    except Exception:
        return []
    for index in range(rectangle_count):
        try:
            bounds = text_page.get_rect(index)
            text, _ = _clean_text(text_page.get_text_bounded(*bounds))
            text = " ".join(text.split())
            if not text:
                continue
            regions.append(
                _TextRegion(
                    text=text,
                    bbox=_top_left_bbox(bounds, width=width, height=height),
                    object_count=0,
                    extraction_source="text_rectangle",
                )
            )
        except Exception:
            continue
    return _merge_text_objects(regions)


def _vector_table_layout(page: pypdfium2.PdfPage) -> bool:
    horizontal_segments = 0
    vertical_segments = 0
    path_count = 0
    pdfium = pypdfium2.raw
    for path in page.get_objects(filter=[pdfium.FPDF_PAGEOBJ_PATH]):
        path_count += 1
        object_horizontal = 0
        object_vertical = 0
        previous: tuple[float, float] | None = None
        try:
            segment_count = pdfium.FPDFPath_CountSegments(path)
            for index in range(segment_count):
                segment = pdfium.FPDFPath_GetPathSegment(path, index)
                if not segment:
                    continue
                x = c_float()
                y = c_float()
                if not pdfium.FPDFPathSegment_GetPoint(segment, x, y):
                    continue
                point = (float(x.value), float(y.value))
                segment_type = pdfium.FPDFPathSegment_GetType(segment)
                if segment_type == pdfium.FPDF_SEGMENT_LINETO and previous is not None:
                    delta_x = abs(point[0] - previous[0])
                    delta_y = abs(point[1] - previous[1])
                    if delta_x >= 18.0 and delta_y <= 1.5:
                        object_horizontal += 1
                    elif delta_y >= 18.0 and delta_x <= 1.5:
                        object_vertical += 1
                previous = point
        except Exception:
            object_horizontal = 0
            object_vertical = 0

        if not object_horizontal and not object_vertical:
            try:
                left, bottom, right, top = path.get_bounds()
                path_width = abs(float(right) - float(left))
                path_height = abs(float(top) - float(bottom))
                if path_width >= 36.0 and path_height <= 2.0:
                    object_horizontal = 1
                elif path_height >= 36.0 and path_width <= 2.0:
                    object_vertical = 1
            except Exception:
                pass
        horizontal_segments += object_horizontal
        vertical_segments += object_vertical

    return (
        path_count >= 1
        and horizontal_segments >= 3
        and vertical_segments >= 3
        and horizontal_segments + vertical_segments >= 6
    )


def _page_sections(
    regions: list[_TextRegion],
    *,
    page_number: int,
    section_path: list[str],
) -> list[ParsedSection]:
    sections: list[ParsedSection] = []
    table_row = 0
    for line_number, region in enumerate(regions, start=1):
        rendered = region.text
        if _is_table_line(rendered):
            table_row += 1
            rendered = rendered.replace("\t", " | ")
            if not rendered.startswith("|"):
                rendered = f"| {rendered} |"
            block_type = BlockType.TABLE
        else:
            block_type = BlockType.HEADING if _HEADING.fullmatch(rendered) else BlockType.PDF_TEXT
            if block_type == BlockType.HEADING:
                section_path[:] = [rendered]
        sections.append(
            ParsedSection(
                text=rendered,
                block_type=block_type,
                page_number=page_number,
                section_path=tuple(section_path),
                bbox=region.bbox,
                table_row=table_row if block_type == BlockType.TABLE else None,
                metadata={
                    "line_start": line_number,
                    "line_end": line_number,
                    "text_object_count": region.object_count,
                    "extraction_source": region.extraction_source,
                    "coordinate_space": "pdf_points_top_left",
                    "bbox_precision": "object_line",
                    "locator_version": "pdf-text-object-v1",
                    "table_structure_used": False,
                },
            )
        )
    return sections


def parse_plain_pdf(content: bytes) -> PlainPdfResult:
    document, page_count = validate_pdf_preflight(content)
    sections: list[ParsedSection] = []
    warnings: list[ParseWarning] = []
    requires_deepdoc = False
    section_path: list[str] = []
    try:
        for page_index in range(page_count):
            page = document[page_index]
            text_page = None
            try:
                width, height = (float(value) for value in page.get_size())
                text_page = page.get_textpage()
                raw_text = text_page.get_text_range()
                text, control_count = _clean_text(raw_text)
                if control_count:
                    warnings.append(
                        ParseWarning(
                            code="pdf_control_characters_removed",
                            message=f"Removed {control_count} PDF control character(s)",
                            page_number=page_index + 1,
                        )
                    )
                visible = sum(character.isalnum() for character in text)
                if visible < 3:
                    requires_deepdoc = True
                    warnings.append(
                        ParseWarning(
                            code="pdf_plain_text_insufficient",
                            message="Page requires OCR/layout parsing",
                            page_number=page_index + 1,
                        )
                    )
                    continue
                if _vector_table_layout(page):
                    requires_deepdoc = True
                    warnings.append(
                        ParseWarning(
                            code="pdf_vector_table_detected",
                            message="Vector grid requires DeepDOC table structure parsing",
                            page_number=page_index + 1,
                        )
                    )
                page_regions = _extract_text_regions(
                    page,
                    text_page,
                    width=width,
                    height=height,
                )
                if not page_regions:
                    requires_deepdoc = True
                    warnings.append(
                        ParseWarning(
                            code="pdf_text_locator_unavailable",
                            message="Text exists but no stable object locator could be extracted",
                            page_number=page_index + 1,
                        )
                    )
                    continue
                sections.extend(
                    _page_sections(
                        page_regions,
                        page_number=page_index + 1,
                        section_path=section_path,
                    )
                )
            except IngestionError:
                raise
            except Exception:
                requires_deepdoc = True
                warnings.append(
                    ParseWarning(
                        code="pdf_plain_extraction_failed",
                        message="Page text extraction failed and requires DeepDOC",
                        page_number=page_index + 1,
                    )
                )
            finally:
                if text_page is not None:
                    text_page.close()
                page.close()
    finally:
        document.close()
    return PlainPdfResult(
        sections=tuple(sections),
        warnings=tuple(warnings),
        page_count=page_count,
        requires_deepdoc=requires_deepdoc,
    )


__all__ = [
    "PDF_MAX_BYTES",
    "PDF_MAX_PAGES",
    "PDF_PLAIN_PARSER_VERSION",
    "PlainPdfResult",
    "parse_plain_pdf",
    "validate_pdf_preflight",
]
