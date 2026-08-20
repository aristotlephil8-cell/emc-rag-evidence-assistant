# Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
# Licensed under the Apache License, Version 2.0 (the "License").
# Modified by the CVRAG contributors for a bounded, evidence-locating parser.

"""Pinned InfiniFlow DeepDOC inference adapted for CVRAG.

The OCR, layout, and table-model preprocessing follows the Apache-2.0
InfiniFlow DeepDOC/RAGFlow implementation. CVRAG changes the output contract,
resource limits, model verification, and failure handling. See
``THIRD_PARTY_NOTICES.md`` in this package.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

import cv2
import numpy as np
import onnxruntime
import pyclipper

from app.ingestion.assets import validate_deepdoc_assets
from app.ingestion.errors import IngestionError, IngestionErrorCode
from app.ingestion.pdf_plain import validate_pdf_preflight
from app.ingestion.versions import DEEPDOC_PARSER_VERSION, DEEPDOC_REVISION
from app.schemas.ingestion import BlockType, FileType, ParsedDocument, ParsedSection, ParseWarning

DEEPDOC_DPI = 216
DEEPDOC_TIMEOUT_SECONDS = 120.0
DEEPDOC_MAX_PAGE_PIXELS = 20_000_000
DEEPDOC_MAX_TOTAL_PIXELS = 50_000_000
DEEPDOC_MAX_DECODED_IMAGE_BYTES = 160 * 1024 * 1024
DEEPDOC_MAX_OCR_CONTOURS = 1000

_LAYOUT_LABELS = (
    "title",
    "text",
    "reference",
    "figure",
    "figure caption",
    "table",
    "table caption",
    "table caption",
    "equation",
    "figure caption",
)
_TABLE_LABELS = (
    "table",
    "table column",
    "table row",
    "table column header",
    "table projected row header",
    "table spanning cell",
    "table grid cell",
)


@dataclass(frozen=True)
class _OcrBox:
    text: str
    confidence: float
    bbox_pixels: tuple[float, float, float, float]


@dataclass(frozen=True)
class _LayoutBox:
    label: str
    confidence: float
    bbox_pixels: tuple[float, float, float, float]


def _session(path: Path) -> onnxruntime.InferenceSession:
    options = onnxruntime.SessionOptions()
    options.enable_cpu_mem_arena = False
    options.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 2
    return onnxruntime.InferenceSession(
        str(path),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )


def _ordered_quad(points: np.ndarray) -> np.ndarray:
    points = np.asarray(points, dtype=np.float32)
    ordered = np.zeros((4, 2), dtype=np.float32)
    sums = points.sum(axis=1)
    differences = np.diff(points, axis=1).reshape(-1)
    ordered[0] = points[np.argmin(sums)]
    ordered[2] = points[np.argmax(sums)]
    ordered[1] = points[np.argmin(differences)]
    ordered[3] = points[np.argmax(differences)]
    return ordered


def _resize_detection_image(image: np.ndarray) -> tuple[np.ndarray, float, float]:
    height, width = image.shape[:2]
    ratio = min(1.0, 960.0 / max(height, width))
    resized_height = max(32, int(round(height * ratio / 32) * 32))
    resized_width = max(32, int(round(width * ratio / 32) * 32))
    resized = cv2.resize(image, (resized_width, resized_height))
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    rgb -= np.array([0.485, 0.456, 0.406], dtype=np.float32)
    rgb /= np.array([0.229, 0.224, 0.225], dtype=np.float32)
    tensor = rgb.transpose(2, 0, 1)[None, ...].astype(np.float32)
    return tensor, height / resized_height, width / resized_width


def _polygon_score(probability: np.ndarray, points: np.ndarray) -> float:
    height, width = probability.shape
    x0 = max(0, int(np.floor(points[:, 0].min())))
    x1 = min(width - 1, int(np.ceil(points[:, 0].max())))
    y0 = max(0, int(np.floor(points[:, 1].min())))
    y1 = min(height - 1, int(np.ceil(points[:, 1].max())))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    mask = np.zeros((y1 - y0 + 1, x1 - x0 + 1), dtype=np.uint8)
    shifted = points.copy()
    shifted[:, 0] -= x0
    shifted[:, 1] -= y0
    cv2.fillPoly(mask, [shifted.astype(np.int32)], 1)
    return float(cv2.mean(probability[y0 : y1 + 1, x0 : x1 + 1], mask=mask)[0])


def _unclip(points: np.ndarray, ratio: float = 1.5) -> np.ndarray | None:
    contour = points.astype(np.float32)
    area = abs(float(cv2.contourArea(contour)))
    perimeter = float(cv2.arcLength(contour, True))
    if area <= 0 or perimeter <= 0:
        return None
    offset = pyclipper.PyclipperOffset()
    offset.AddPath(
        contour.astype(np.int64).tolist(),
        pyclipper.JT_ROUND,
        pyclipper.ET_CLOSEDPOLYGON,
    )
    expanded = offset.Execute(area * ratio / perimeter)
    if len(expanded) != 1:
        return None
    return np.asarray(expanded[0], dtype=np.float32)


def _detect_text_boxes(
    image: np.ndarray,
    detector: onnxruntime.InferenceSession,
) -> list[np.ndarray]:
    tensor, height_scale, width_scale = _resize_detection_image(image)
    probability = np.asarray(detector.run(None, {detector.get_inputs()[0].name: tensor})[0])[0, 0]
    bitmap = (probability > 0.3).astype(np.uint8) * 255
    contours, _ = cv2.findContours(bitmap, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    if len(contours) > DEEPDOC_MAX_OCR_CONTOURS:
        raise IngestionError(
            IngestionErrorCode.RESOURCE_LIMIT_EXCEEDED,
            "PDF page contains too many OCR candidates",
        )
    candidates: list[tuple[float, np.ndarray]] = []
    for contour in contours:
        rectangle = cv2.minAreaRect(contour)
        points = cv2.boxPoints(rectangle)
        score = _polygon_score(probability, points)
        if min(rectangle[1]) < 3 or score < 0.5:
            continue
        expanded = _unclip(points)
        if expanded is None:
            continue
        rectangle = cv2.minAreaRect(expanded)
        if min(rectangle[1]) < 3:
            continue
        quad = _ordered_quad(cv2.boxPoints(rectangle))
        quad[:, 0] *= width_scale
        quad[:, 1] *= height_scale
        quad[:, 0] = np.clip(quad[:, 0], 0, image.shape[1] - 1)
        quad[:, 1] = np.clip(quad[:, 1], 0, image.shape[0] - 1)
        candidates.append((score, quad))
    candidates.sort(
        key=lambda item: (
            round(float(item[1][:, 1].min()), 3),
            round(float(item[1][:, 0].min()), 3),
            -item[0],
        )
    )
    return [quad for _, quad in candidates]


def _crop_quad(image: np.ndarray, quad: np.ndarray) -> np.ndarray:
    quad = _ordered_quad(quad)
    width = max(
        1,
        int(round(max(np.linalg.norm(quad[0] - quad[1]), np.linalg.norm(quad[2] - quad[3])))),
    )
    height = max(
        1,
        int(round(max(np.linalg.norm(quad[0] - quad[3]), np.linalg.norm(quad[1] - quad[2])))),
    )
    target = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    matrix = cv2.getPerspectiveTransform(quad, target)
    crop = cv2.warpPerspective(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    if crop.shape[0] / max(crop.shape[1], 1) >= 1.5:
        crop = np.rot90(crop)
    return np.ascontiguousarray(crop)


def _decode_ctc(
    predictions: np.ndarray,
    characters: tuple[str, ...],
) -> tuple[str, float]:
    indexes = predictions.argmax(axis=1)
    scores = predictions.max(axis=1)
    text: list[str] = []
    selected_scores: list[float] = []
    previous = -1
    space_index = len(characters) + 1
    for index, score in zip(indexes.tolist(), scores.tolist(), strict=True):
        if index == 0 or index == previous:
            previous = index
            continue
        previous = index
        if 1 <= index <= len(characters):
            text.append(characters[index - 1])
            selected_scores.append(float(score))
        elif index == space_index:
            text.append(" ")
            selected_scores.append(float(score))
    confidence = sum(selected_scores) / len(selected_scores) if selected_scores else 0.0
    return "".join(text), confidence


def _recognize_text_boxes(
    image: np.ndarray,
    quads: list[np.ndarray],
    recognizer: onnxruntime.InferenceSession,
    characters: tuple[str, ...],
) -> list[_OcrBox]:
    if not quads:
        return []
    crops = [_crop_quad(image, quad) for quad in quads]
    results: list[_OcrBox] = []
    input_name = recognizer.get_inputs()[0].name
    for batch_start in range(0, len(crops), 16):
        batch_crops = crops[batch_start : batch_start + 16]
        max_ratio = max(
            320 / 48,
            *(crop.shape[1] / max(crop.shape[0], 1) for crop in batch_crops),
        )
        width = max(1, int(math.ceil(48 * max_ratio)))
        normalized: list[np.ndarray] = []
        for crop in batch_crops:
            resized_width = min(
                width,
                int(math.ceil(48 * crop.shape[1] / max(crop.shape[0], 1))),
            )
            resized = cv2.resize(crop, (max(1, resized_width), 48)).astype(np.float32)
            tensor = (resized.transpose(2, 0, 1) / 255.0 - 0.5) / 0.5
            padded = np.zeros((3, 48, width), dtype=np.float32)
            padded[:, :, :resized_width] = tensor
            normalized.append(padded)
        predictions = recognizer.run(None, {input_name: np.stack(normalized)})[0]
        for offset, prediction in enumerate(predictions):
            text, confidence = _decode_ctc(prediction, characters)
            if confidence < 0.5 or not text.strip():
                continue
            quad = quads[batch_start + offset]
            results.append(
                _OcrBox(
                    text=text,
                    confidence=confidence,
                    bbox_pixels=(
                        float(quad[:, 0].min()),
                        float(quad[:, 1].min()),
                        float(quad[:, 0].max()),
                        float(quad[:, 1].max()),
                    ),
                )
            )
    return sorted(
        results,
        key=lambda box: (
            round(box.bbox_pixels[1], 3),
            round(box.bbox_pixels[0], 3),
            box.text,
        ),
    )


def _nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> list[int]:
    if not len(boxes):
        return []
    order = np.argsort(-scores, kind="stable")
    keep: list[int] = []
    while order.size:
        index = int(order[0])
        keep.append(index)
        if order.size == 1:
            break
        others = order[1:]
        x0 = np.maximum(boxes[index, 0], boxes[others, 0])
        y0 = np.maximum(boxes[index, 1], boxes[others, 1])
        x1 = np.minimum(boxes[index, 2], boxes[others, 2])
        y1 = np.minimum(boxes[index, 3], boxes[others, 3])
        intersection = np.maximum(0, x1 - x0) * np.maximum(0, y1 - y0)
        area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        union = area[index] + area[others] - intersection
        iou = np.divide(
            intersection,
            union,
            out=np.zeros_like(intersection),
            where=union > 0,
        )
        order = others[iou < threshold]
    return keep


def _letterbox(image: np.ndarray, size: int) -> tuple[np.ndarray, float, float, float]:
    height, width = image.shape[:2]
    ratio = min(size / height, size / width)
    new_width = int(round(width * ratio))
    new_height = int(round(height * ratio))
    pad_x = (size - new_width) / 2
    pad_y = (size - new_height) / 2
    resized = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2RGB), (new_width, new_height))
    top, bottom = int(round(pad_y - 0.1)), int(round(pad_y + 0.1))
    left, right = int(round(pad_x - 0.1)), int(round(pad_x + 0.1))
    padded = cv2.copyMakeBorder(
        resized,
        top,
        bottom,
        left,
        right,
        cv2.BORDER_CONSTANT,
        value=(114, 114, 114),
    )
    tensor = (padded.astype(np.float32) / 255.0).transpose(2, 0, 1)[None, ...]
    return tensor, ratio, pad_x, pad_y


def _detect_layout(
    image: np.ndarray,
    session: onnxruntime.InferenceSession,
) -> list[_LayoutBox]:
    tensor, ratio, pad_x, pad_y = _letterbox(image, 1024)
    output = np.squeeze(session.run(None, {session.get_inputs()[0].name: tensor})[0])
    if output.ndim != 2 or output.shape[1] != 6:
        raise ValueError("unexpected layout output")
    output = output[output[:, 4] > 0.08]
    if not len(output):
        return []
    boxes = output[:, :4].astype(np.float32)
    boxes[:, [0, 2]] = (boxes[:, [0, 2]] - pad_x) / ratio
    boxes[:, [1, 3]] = (boxes[:, [1, 3]] - pad_y) / ratio
    boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0, image.shape[1])
    boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0, image.shape[0])
    scores = output[:, 4]
    class_ids = output[:, 5].astype(int)
    indexes: list[int] = []
    for class_id in sorted(set(class_ids.tolist())):
        members = np.where(class_ids == class_id)[0]
        indexes.extend(members[_nms(boxes[members], scores[members], 0.45)].tolist())
    result = [
        _LayoutBox(
            label=(
                _LAYOUT_LABELS[class_ids[index]]
                if 0 <= class_ids[index] < len(_LAYOUT_LABELS)
                else "text"
            ),
            confidence=float(scores[index]),
            bbox_pixels=tuple(float(value) for value in boxes[index]),
        )
        for index in indexes
        if boxes[index, 2] > boxes[index, 0] and boxes[index, 3] > boxes[index, 1]
    ]
    return sorted(
        result,
        key=lambda box: (
            round(box.bbox_pixels[1], 3),
            round(box.bbox_pixels[0], 3),
            box.label,
            -box.confidence,
        ),
    )


def _detect_table_structure(
    image: np.ndarray,
    session: onnxruntime.InferenceSession,
) -> list[_LayoutBox]:
    height, width = image.shape[:2]
    resized = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2RGB), (640, 640))
    tensor = (resized.astype(np.float32) / 255.0).transpose(2, 0, 1)[None, ...]
    output = np.squeeze(session.run(None, {session.get_inputs()[0].name: tensor})[0]).T
    if output.ndim != 2 or output.shape[1] < 5:
        raise ValueError("unexpected table output")
    scores = output[:, 4:].max(axis=1)
    selected = scores > 0.2
    output = output[selected]
    scores = scores[selected]
    if not len(output):
        return []
    class_ids = output[:, 4:].argmax(axis=1)
    boxes = output[:, :4].copy()
    centers = boxes[:, :2].copy()
    sizes = boxes[:, 2:4].copy()
    boxes[:, :2] = centers - sizes / 2
    boxes[:, 2:4] = centers + sizes / 2
    boxes[:, [0, 2]] *= width / 640
    boxes[:, [1, 3]] *= height / 640
    indexes: list[int] = []
    for class_id in sorted(set(class_ids.tolist())):
        members = np.where(class_ids == class_id)[0]
        indexes.extend(members[_nms(boxes[members], scores[members], 0.2)].tolist())
    return [
        _LayoutBox(
            label=(
                _TABLE_LABELS[class_ids[index]]
                if 0 <= class_ids[index] < len(_TABLE_LABELS)
                else "table grid cell"
            ),
            confidence=float(scores[index]),
            bbox_pixels=tuple(float(value) for value in boxes[index]),
        )
        for index in indexes
    ]


def _overlap_fraction(
    inner: tuple[float, float, float, float],
    outer: tuple[float, float, float, float],
) -> float:
    x0 = max(inner[0], outer[0])
    y0 = max(inner[1], outer[1])
    x1 = min(inner[2], outer[2])
    y1 = min(inner[3], outer[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area = max(1.0, (inner[2] - inner[0]) * (inner[3] - inner[1]))
    return intersection / area


def _union_bbox(boxes: Iterable[_OcrBox]) -> tuple[float, float, float, float]:
    values = list(boxes)
    return (
        min(box.bbox_pixels[0] for box in values),
        min(box.bbox_pixels[1] for box in values),
        max(box.bbox_pixels[2] for box in values),
        max(box.bbox_pixels[3] for box in values),
    )


def _escape_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _fallback_table_cells(boxes: list[_OcrBox]) -> list[list[str]]:
    ordered = sorted(boxes, key=lambda box: (box.bbox_pixels[1], box.bbox_pixels[0], box.text))
    heights = [box.bbox_pixels[3] - box.bbox_pixels[1] for box in ordered]
    tolerance = max(3.0, float(np.median(heights)) * 0.65)
    rows: list[list[_OcrBox]] = []
    for box in ordered:
        center = (box.bbox_pixels[1] + box.bbox_pixels[3]) / 2
        if not rows:
            rows.append([box])
            continue
        previous_center = float(
            np.mean([(value.bbox_pixels[1] + value.bbox_pixels[3]) / 2 for value in rows[-1]])
        )
        if abs(center - previous_center) <= tolerance:
            rows[-1].append(box)
        else:
            rows.append([box])
    cells = [
        [
            _escape_cell(box.text.strip())
            for box in sorted(row, key=lambda item: item.bbox_pixels[0])
        ]
        for row in rows
    ]
    column_count = max(len(row) for row in cells)
    return [row + [""] * (column_count - len(row)) for row in cells]


def _stable_axes(structures: list[_LayoutBox], label: str) -> list[_LayoutBox]:
    axis = 1 if label == "table row" else 0
    candidates = [box for box in structures if box.label == label and box.confidence >= 0.35]
    candidates.sort(
        key=lambda box: (
            (box.bbox_pixels[axis] + box.bbox_pixels[axis + 2]) / 2,
            -box.confidence,
            box.bbox_pixels,
        )
    )
    selected: list[_LayoutBox] = []
    for candidate in candidates:
        start = candidate.bbox_pixels[axis]
        end = candidate.bbox_pixels[axis + 2]
        handled = False
        for index, current in enumerate(selected):
            current_start = current.bbox_pixels[axis]
            current_end = current.bbox_pixels[axis + 2]
            overlap = max(0.0, min(end, current_end) - max(start, current_start))
            denominator = max(1.0, min(end - start, current_end - current_start))
            if overlap / denominator >= 0.75:
                if candidate.confidence > current.confidence:
                    selected[index] = candidate
                handled = True
                break
        if not handled:
            selected.append(candidate)
    return sorted(
        selected,
        key=lambda box: (
            (box.bbox_pixels[axis] + box.bbox_pixels[axis + 2]) / 2,
            box.bbox_pixels,
        ),
    )


def _axis_index(
    box: _OcrBox,
    axes: list[_LayoutBox],
    *,
    axis: int,
    crop_origin: tuple[float, float],
) -> int | None:
    offset = crop_origin[axis]
    start = box.bbox_pixels[axis] - offset
    end = box.bbox_pixels[axis + 2] - offset
    center = (start + end) / 2
    containing = [
        (index, candidate)
        for index, candidate in enumerate(axes)
        if candidate.bbox_pixels[axis] <= center <= candidate.bbox_pixels[axis + 2]
    ]
    if containing:
        return min(
            containing,
            key=lambda item: (
                abs(center - (item[1].bbox_pixels[axis] + item[1].bbox_pixels[axis + 2]) / 2),
                -item[1].confidence,
                item[0],
            ),
        )[0]
    overlaps = [
        max(
            0.0,
            min(end, candidate.bbox_pixels[axis + 2]) - max(start, candidate.bbox_pixels[axis]),
        )
        for candidate in axes
    ]
    if not overlaps or max(overlaps) <= 0:
        return None
    return int(np.argmax(np.asarray(overlaps)))


def _structured_table_cells(
    boxes: list[_OcrBox],
    structures: list[_LayoutBox],
    *,
    crop_origin: tuple[float, float],
) -> list[list[str]] | None:
    if any(box.label == "table spanning cell" for box in structures):
        return None
    rows = _stable_axes(structures, "table row")
    columns = _stable_axes(structures, "table column")
    if not rows or not columns:
        return None
    cells: list[list[list[_OcrBox]]] = [[[] for _ in range(len(columns))] for _ in range(len(rows))]
    for box in boxes:
        row_index = _axis_index(box, rows, axis=1, crop_origin=crop_origin)
        column_index = _axis_index(box, columns, axis=0, crop_origin=crop_origin)
        if row_index is None or column_index is None:
            return None
        cells[row_index][column_index].append(box)
    rendered: list[list[str]] = []
    for row in cells:
        values: list[str] = []
        for cell in row:
            ordered = sorted(
                cell,
                key=lambda value: (
                    value.bbox_pixels[1],
                    value.bbox_pixels[0],
                    value.text,
                ),
            )
            values.append(_escape_cell(" ".join(value.text.strip() for value in ordered).strip()))
        rendered.append(values)
    return rendered


def _points_bbox(
    bbox: tuple[float, float, float, float],
    scale: float,
    page_width: float,
    page_height: float,
) -> tuple[float, float, float, float]:
    epsilon = 1e-6
    x0 = max(0.0, min(bbox[0] / scale, page_width - epsilon))
    y0 = max(0.0, min(bbox[1] / scale, page_height - epsilon))
    x1 = max(x0 + epsilon, min(bbox[2] / scale, page_width))
    y1 = max(y0 + epsilon, min(bbox[3] / scale, page_height))
    return (x0, y0, x1, y1)


def parse_deepdoc_pdf(
    filename: str,
    content: bytes,
    model_dir: Path,
) -> ParsedDocument:
    document, page_count = validate_pdf_preflight(content)
    runtime_sessions: dict[Path, onnxruntime.InferenceSession] = {}

    def load_session(path: Path) -> onnxruntime.InferenceSession:
        session = _session(path)
        runtime_sessions[path] = session
        return session

    try:
        bundle = validate_deepdoc_assets(
            model_dir,
            onnx_session_factory=load_session,
        )
        detector = runtime_sessions[bundle.path_for("ocr_detection")]
        recognizer = runtime_sessions[bundle.path_for("ocr_recognition")]
        layout_session = runtime_sessions[bundle.path_for("layout_detection")]
        table_session = runtime_sessions[bundle.path_for("table_structure_detection")]
        characters = tuple(bundle.path_for("ocr_charset").read_text(encoding="utf-8").splitlines())
    except MemoryError as exc:
        document.close()
        raise IngestionError(
            IngestionErrorCode.RESOURCE_LIMIT_EXCEEDED,
            "DeepDOC model loading exceeded the memory limit",
        ) from exc
    except Exception:
        document.close()
        raise

    sections: list[ParsedSection] = []
    warnings: list[ParseWarning] = []
    section_path: list[str] = []
    started_at = monotonic()
    total_pixels = 0
    decoded_bytes = 0
    scale = DEEPDOC_DPI / 72.0

    try:
        for page_index in range(page_count):
            if monotonic() - started_at > DEEPDOC_TIMEOUT_SECONDS:
                raise IngestionError(
                    IngestionErrorCode.PARSE_TIMEOUT,
                    "DeepDOC parsing exceeded the 120 second limit",
                )
            page = document[page_index]
            try:
                page_width, page_height = (float(value) for value in page.get_size())
                pixel_width = int(math.ceil(page_width * scale))
                pixel_height = int(math.ceil(page_height * scale))
                page_pixels = pixel_width * pixel_height
                if page_pixels > DEEPDOC_MAX_PAGE_PIXELS:
                    raise IngestionError(
                        IngestionErrorCode.RESOURCE_LIMIT_EXCEEDED,
                        "PDF rendered page exceeds the pixel limit",
                    )
                total_pixels += page_pixels
                decoded_bytes += page_pixels * 3
                if (
                    total_pixels > DEEPDOC_MAX_TOTAL_PIXELS
                    or decoded_bytes > DEEPDOC_MAX_DECODED_IMAGE_BYTES
                ):
                    raise IngestionError(
                        IngestionErrorCode.RESOURCE_LIMIT_EXCEEDED,
                        "PDF rendered pages exceed the image memory limit",
                    )
                bitmap = page.render(scale=scale)
                pixels = np.asarray(bitmap.to_numpy())
                if pixels.ndim == 2:
                    image = cv2.cvtColor(pixels, cv2.COLOR_GRAY2BGR)
                elif pixels.ndim == 3 and pixels.shape[2] == 3:
                    image = np.ascontiguousarray(pixels)
                elif pixels.ndim == 3 and pixels.shape[2] == 4:
                    image = np.ascontiguousarray(pixels[:, :, :3])
                else:
                    raise ValueError("unexpected PDFium bitmap shape")
                del bitmap, pixels

                try:
                    quads = _detect_text_boxes(image, detector)
                    ocr_boxes = _recognize_text_boxes(
                        image,
                        quads,
                        recognizer,
                        characters,
                    )
                except IngestionError:
                    raise
                except Exception as exc:
                    raise IngestionError(
                        IngestionErrorCode.PARSE_FAILED,
                        "DeepDOC OCR failed",
                    ) from exc
                if not ocr_boxes:
                    if float(image.std()) < 2.0:
                        warnings.append(
                            ParseWarning(
                                code="pdf_blank_page",
                                message="PDF page is blank",
                                page_number=page_index + 1,
                            )
                        )
                        continue
                    raise IngestionError(
                        IngestionErrorCode.PARSE_FAILED,
                        "DeepDOC OCR produced no usable text",
                    )

                try:
                    layout_boxes = _detect_layout(image, layout_session)
                except Exception:
                    layout_boxes = []
                if not layout_boxes:
                    warnings.append(
                        ParseWarning(
                            code="pdf_layout_fallback",
                            message="Layout model produced no regions; OCR order was used",
                            page_number=page_index + 1,
                        )
                    )

                ordered_regions: list[
                    tuple[float, float, str, list[_OcrBox], _LayoutBox | None]
                ] = []
                consumed: set[_OcrBox] = set()
                for layout in layout_boxes:
                    if layout.label != "table":
                        continue
                    values = [
                        box
                        for box in ocr_boxes
                        if box not in consumed
                        and _overlap_fraction(box.bbox_pixels, layout.bbox_pixels) >= 0.3
                    ]
                    if values:
                        consumed.update(values)
                        raw_bbox = _union_bbox(values)
                        ordered_regions.append((raw_bbox[1], raw_bbox[0], "table", values, layout))
                for box in ocr_boxes:
                    if box in consumed:
                        continue
                    candidates = [
                        (_overlap_fraction(box.bbox_pixels, layout.bbox_pixels), layout)
                        for layout in layout_boxes
                        if layout.label != "table"
                    ]
                    score, selected_layout = max(
                        candidates,
                        key=lambda item: (item[0], item[1].confidence, item[1].label),
                        default=(0.0, None),
                    )
                    label = (
                        selected_layout.label
                        if score >= 0.3 and selected_layout is not None
                        else "text"
                    )
                    ordered_regions.append(
                        (
                            box.bbox_pixels[1],
                            box.bbox_pixels[0],
                            label,
                            [box],
                            selected_layout,
                        )
                    )
                ordered_regions.sort(
                    key=lambda item: (
                        round(item[0], 3),
                        round(item[1], 3),
                        item[2],
                    )
                )

                table_index = 0
                for region_index, (_, _, label, values, layout) in enumerate(
                    ordered_regions,
                    start=1,
                ):
                    raw_bbox = _union_bbox(values)
                    bbox = _points_bbox(raw_bbox, scale, page_width, page_height)
                    if label == "table":
                        table_index += 1
                        x0, y0, x1, y1 = layout.bbox_pixels if layout is not None else raw_bbox
                        crop_left = max(0, int(x0))
                        crop_top = max(0, int(y0))
                        crop_right = min(image.shape[1], int(math.ceil(x1)))
                        crop_bottom = min(image.shape[0], int(math.ceil(y1)))
                        crop = image[crop_top:crop_bottom, crop_left:crop_right]
                        try:
                            structures = _detect_table_structure(crop, table_session)
                        except Exception:
                            structures = []
                        cells = _structured_table_cells(
                            values,
                            structures,
                            crop_origin=(float(crop_left), float(crop_top)),
                        )
                        structure_used = cells is not None
                        if cells is None:
                            cells = _fallback_table_cells(values)
                            warnings.append(
                                ParseWarning(
                                    code="pdf_table_fallback",
                                    message="Table structure fell back to OCR row grouping",
                                    page_number=page_index + 1,
                                )
                            )
                        column_count = max(len(row) for row in cells)
                        for row_number, row in enumerate(cells, start=1):
                            sections.append(
                                ParsedSection(
                                    text="| " + " | ".join(row) + " |",
                                    block_type=BlockType.TABLE,
                                    page_number=page_index + 1,
                                    section_path=tuple(section_path),
                                    bbox=bbox,
                                    table_row=row_number,
                                    metadata={
                                        "region_index": region_index,
                                        "table_index": table_index,
                                        "row_count": len(cells),
                                        "column_count": column_count,
                                        "table_structure_used": structure_used,
                                        "layout_type": label,
                                        "coordinate_space": "pdf_points_top_left",
                                    },
                                )
                            )
                        continue

                    text = " ".join(value.text.strip() for value in values).strip()
                    if not text:
                        continue
                    if label == "title":
                        block_type = BlockType.HEADING
                        section_path[:] = [text]
                    elif "caption" in label:
                        block_type = BlockType.IMAGE_CAPTION
                    else:
                        block_type = BlockType.OCR_TEXT
                    sections.append(
                        ParsedSection(
                            text=text,
                            block_type=block_type,
                            page_number=page_index + 1,
                            section_path=tuple(section_path),
                            bbox=bbox,
                            metadata={
                                "region_index": region_index,
                                "layout_type": label,
                                "ocr_confidence": round(
                                    sum(value.confidence for value in values) / len(values),
                                    6,
                                ),
                                "coordinate_space": "pdf_points_top_left",
                            },
                        )
                    )
            finally:
                page.close()
    except IngestionError:
        raise
    except MemoryError as exc:
        raise IngestionError(
            IngestionErrorCode.RESOURCE_LIMIT_EXCEEDED,
            "DeepDOC parsing exceeded the memory limit",
        ) from exc
    except Exception as exc:
        raise IngestionError(
            IngestionErrorCode.PARSE_FAILED,
            "DeepDOC parsing failed",
        ) from exc
    finally:
        document.close()

    if not sections:
        raise IngestionError(
            IngestionErrorCode.DOCUMENT_EMPTY,
            "PDF document contains no usable OCR content",
        )
    return ParsedDocument(
        filename=filename,
        file_type=FileType.PDF,
        parser_name="infiniflow_deepdoc_onnx",
        parser_version=DEEPDOC_PARSER_VERSION,
        sections=tuple(sections),
        warnings=tuple(warnings),
        metadata={
            "route": "deepdoc",
            "page_count": page_count,
            "dpi": DEEPDOC_DPI,
            "model_revision": DEEPDOC_REVISION,
            "asset_set": bundle.asset_set,
            "coordinate_space": "pdf_points_top_left",
        },
    )


__all__ = [
    "DEEPDOC_DPI",
    "DEEPDOC_PARSER_VERSION",
    "DEEPDOC_TIMEOUT_SECONDS",
    "parse_deepdoc_pdf",
]
