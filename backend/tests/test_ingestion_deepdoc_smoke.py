from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.ingestion import ParserService
from app.schemas.ingestion import BlockType


@pytest.mark.deepdoc
def test_real_deepdoc_scanned_pdf_smoke() -> None:
    model_value = os.environ.get("CVRAG_DEEPDOC_MODEL_DIR")
    sample_value = os.environ.get("CVRAG_DEEPDOC_SCAN_PDF")
    if not model_value or not sample_value:
        pytest.skip("set CVRAG_DEEPDOC_MODEL_DIR and CVRAG_DEEPDOC_SCAN_PDF")
    model_dir = Path(model_value)
    sample = Path(sample_value)

    parsed = ParserService.parse(sample.name, sample.read_bytes(), model_dir)

    assert parsed.metadata["route"] == "deepdoc"
    assert parsed.metadata["model_revision"]
    assert any(
        section.block_type in {BlockType.OCR_TEXT, BlockType.HEADING} for section in parsed.sections
    )
    assert all(section.page_number is not None for section in parsed.sections)
    assert all(section.bbox is not None for section in parsed.sections)
    assert all(
        section.metadata.get("coordinate_space") == "pdf_points_top_left"
        for section in parsed.sections
    )


@pytest.mark.deepdoc
def test_real_deepdoc_table_structure_smoke() -> None:
    model_value = os.environ.get("CVRAG_DEEPDOC_MODEL_DIR")
    sample_value = os.environ.get("CVRAG_DEEPDOC_TABLE_PDF")
    if not model_value or not sample_value:
        pytest.skip("set CVRAG_DEEPDOC_MODEL_DIR and CVRAG_DEEPDOC_TABLE_PDF")
    model_dir = Path(model_value)
    sample = Path(sample_value)

    parsed = ParserService.parse(sample.name, sample.read_bytes(), model_dir)
    table_sections = [
        section for section in parsed.sections if section.block_type == BlockType.TABLE
    ]

    assert parsed.metadata["route"] == "deepdoc"
    assert table_sections
    assert all(section.table_row is not None for section in table_sections)
    assert all(section.bbox is not None for section in table_sections)
    assert any(section.metadata.get("table_structure_used") is True for section in table_sections)
