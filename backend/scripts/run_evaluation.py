from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from collections.abc import Iterable
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pypdfium2

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from evaluation.retrieval_metrics import evaluate_records, load_jsonl  # noqa: E402
from evaluation.validate_dataset import validate_repository  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import DocumentRepository  # noqa: E402
from app.models import (  # noqa: E402
    CitationSource,
    RetrievalRequest,
    RetrievalVariant,
    ScoreTrace,
    SearchHit,
)
from app.pipeline import runtime_config_hash, runtime_evaluation_config  # noqa: E402
from app.providers import ProviderError, build_provider  # noqa: E402
from app.search import Candidate, RetrievalService  # noqa: E402
from app.services import DocumentService, build_elasticsearch_index  # noqa: E402

VARIANTS = (
    RetrievalVariant.BM25,
    RetrievalVariant.VECTOR,
    RetrievalVariant.HYBRID_RRF,
    RetrievalVariant.HYBRID_RRF_RERANK,
    RetrievalVariant.ROUTED_HYBRID_RRF_RERANK,
)
FINAL_VARIANT = RetrievalVariant.ROUTED_HYBRID_RRF_RERANK
THRESHOLD_GRID = tuple(round(index * 0.05, 2) for index in range(21))


class LocatorResolutionError(ValueError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _corpus_hash() -> str:
    manifest = _read_json(REPOSITORY_ROOT / "evaluation" / "freeze_manifest.json")
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _intersection_over_union(first: list[float], second: list[float]) -> float:
    x0 = max(first[0], second[0])
    y0 = max(first[1], second[1])
    x1 = min(first[2], second[2])
    y1 = min(first[3], second[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    union = first_area + second_area - intersection
    return intersection / union if union else 0.0


class LocatorResolver:
    """Resolve chunks to frozen evidence using locators, never query similarity."""

    def __init__(self, corpus_manifest: dict[str, Any]):
        self.documents = {
            document["filename"]: document for document in corpus_manifest["documents"]
        }
        self.evidence_by_section: dict[tuple[str, str], list[str]] = {}
        for document in corpus_manifest["documents"]:
            for section in document["sections"]:
                self.evidence_by_section[(document["filename"], section["section_id"])] = list(
                    section["evidence_ids"]
                )
        self.page_sizes: dict[tuple[str, int], tuple[float, float]] = {}
        for filename, document in self.documents.items():
            if document["format"] != "pdf":
                continue
            artifact_path = REPOSITORY_ROOT / document["artifact"]["path"]
            with pypdfium2.PdfDocument(artifact_path) as pdf:
                for page_index in range(len(pdf)):
                    page = pdf[page_index]
                    try:
                        self.page_sizes[(filename, page_index + 1)] = tuple(
                            float(value) for value in page.get_size()
                        )
                    finally:
                        page.close()

    def _normalized_pdf_bbox(self, hit: SearchHit) -> tuple[list[float], float] | None:
        if hit.source.page_number is None or hit.source.bbox is None:
            return None
        page_size = self.page_sizes.get((hit.source.filename, hit.source.page_number))
        if page_size is None:
            return None
        width, height = page_size
        bbox = [
            hit.source.bbox[0] / width,
            hit.source.bbox[1] / height,
            hit.source.bbox[2] / width,
            hit.source.bbox[3] / height,
        ]
        area = max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])
        return (bbox, area) if area > 0 else None

    def _covers_gold_bbox(self, hit: SearchHit, gold_bbox: object) -> bool:
        normalized = self._normalized_pdf_bbox(hit)
        if normalized is None or not isinstance(gold_bbox, list) or len(gold_bbox) != 4:
            return False
        bbox, hit_area = normalized
        intersection_width = max(0.0, min(bbox[2], gold_bbox[2]) - max(bbox[0], gold_bbox[0]))
        intersection_height = max(0.0, min(bbox[3], gold_bbox[3]) - max(bbox[1], gold_bbox[1]))
        return intersection_width * intersection_height / hit_area >= 0.5

    def _section(self, hit: SearchHit) -> dict[str, Any] | None:
        document = self.documents.get(hit.source.filename)
        if document is None or document.get("role") != "primary":
            return None
        sections = list(document["sections"])
        parser_locator = hit.source.parser_locator

        if hit.source.table_row is not None:
            if hit.source.table_index is None:
                return None
            matches = [
                section
                for section in sections
                if section["region"].get("kind") == "table_row"
                and section["region"].get("table_row") == hit.source.table_row
                and section["region"].get("table_index", 1) == hit.source.table_index
                and section["page_number"] == hit.source.page_number
            ]
            if len(matches) > 1:
                raise LocatorResolutionError(f"ambiguous table row locator: {hit.chunk_id}")
            if matches and self._covers_gold_bbox(hit, matches[0]["region"].get("page_bbox")):
                return matches[0]
            return None

        paragraph_index = parser_locator.get("paragraph_index")
        if isinstance(paragraph_index, int):
            matches = [
                section
                for section in sections
                if section["region"].get("kind") == "paragraph"
                and section["region"].get("paragraph_index") == paragraph_index
            ]
            if len(matches) > 1:
                raise LocatorResolutionError(f"ambiguous paragraph locator: {hit.chunk_id}")
            if matches:
                return matches[0]

        line_start = parser_locator.get("line_start")
        line_end = parser_locator.get("line_end")
        if isinstance(line_start, int) and isinstance(line_end, int):
            matches = [
                section
                for section in sections
                if section["region"].get("kind") == "line_span"
                and section["region"].get("line_start") == line_start
                and section["region"].get("line_end") == line_end
            ]
            if len(matches) > 1:
                raise LocatorResolutionError(f"ambiguous line locator: {hit.chunk_id}")
            if matches:
                return matches[0]

        normalized = self._normalized_pdf_bbox(hit)
        if normalized is None:
            return None
        normalized_bbox, hit_area = normalized
        candidates: list[tuple[float, dict[str, Any]]] = []
        overlapping_sections = 0
        for section in sections:
            gold_bbox = section["region"].get("page_bbox")
            if section["page_number"] != hit.source.page_number or not isinstance(gold_bbox, list):
                continue
            score = _intersection_over_union(normalized_bbox, gold_bbox)
            intersection_width = max(
                0.0,
                min(normalized_bbox[2], gold_bbox[2]) - max(normalized_bbox[0], gold_bbox[0]),
            )
            intersection_height = max(
                0.0,
                min(normalized_bbox[3], gold_bbox[3]) - max(normalized_bbox[1], gold_bbox[1]),
            )
            if intersection_width > 0 and intersection_height > 0:
                overlapping_sections += 1
            coverage = intersection_width * intersection_height / hit_area
            if coverage < 0.5:
                continue
            score += coverage
            candidates.append((score, section))
        candidates.sort(key=lambda item: (-item[0], item[1]["section_id"]))
        if not candidates or candidates[0][0] <= 0:
            if overlapping_sections > 1:
                raise LocatorResolutionError(
                    f"broad PDF locator overlaps multiple frozen regions: {hit.chunk_id}"
                )
            return None
        if len(candidates) > 1 and abs(candidates[0][0] - candidates[1][0]) < 1e-9:
            raise LocatorResolutionError(f"ambiguous PDF locator: {hit.chunk_id}")
        return candidates[0][1]

    def evidence_ids(self, hit: SearchHit) -> list[str]:
        section = self._section(hit)
        if section is None:
            return [f"chunk:{hit.chunk_id}"]
        return self.evidence_by_section[(hit.source.filename, section["section_id"])]


def _candidate_hit(candidate: Candidate, rank: int) -> SearchHit:
    return SearchHit(
        chunk_id=candidate.chunk_id,
        text=candidate.text,
        source=candidate.source,
        rank=rank,
        score=candidate.score,
        scores=ScoreTrace(),
    )


def _runtime_locator_ledger(
    candidates: list[Candidate],
    resolver: LocatorResolver,
    facts: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = {fact["evidence_id"] for fact in facts}
    evidence_chunks: dict[str, list[str]] = {evidence_id: [] for evidence_id in expected}
    noise_chunks = 0
    for rank, candidate in enumerate(candidates, start=1):
        resolved = resolver.evidence_ids(_candidate_hit(candidate, rank))
        matched = False
        for evidence_id in resolved:
            if evidence_id not in evidence_chunks:
                continue
            matched = True
            if candidate.chunk_id not in evidence_chunks[evidence_id]:
                evidence_chunks[evidence_id].append(candidate.chunk_id)
        noise_chunks += int(not matched)
    missing = sorted(
        evidence_id for evidence_id, chunk_ids in evidence_chunks.items() if not chunk_ids
    )
    return {
        "expected_evidence": len(expected),
        "mapped_evidence": len(expected) - len(missing),
        "missing_evidence": missing,
        "noise_chunks": noise_chunks,
        "evidence_chunk_cardinality": {
            evidence_id: len(chunk_ids)
            for evidence_id, chunk_ids in sorted(evidence_chunks.items())
        },
    }


class CachedProvider:
    def __init__(self, provider):
        self.provider = provider
        self.evidence_status = provider.evidence_status
        self._embedding_cache: dict[str, list[float]] = {}

    def embed(self, texts):
        missing = [text for text in texts if text not in self._embedding_cache]
        if missing:
            for text, vector in zip(missing, self.provider.embed(missing), strict=True):
                self._embedding_cache[text] = vector
        return [self._embedding_cache[text] for text in texts]

    def rerank(self, query, documents):
        return self.provider.rerank(query, documents)

    def generate(self, query, sources):
        return self.provider.generate(query, sources)

    def close(self):
        self.provider.close()


def _prediction_ids(hits: Iterable[SearchHit], resolver: LocatorResolver) -> list[str]:
    values: list[str] = []
    for hit in hits:
        for evidence_id in resolver.evidence_ids(hit):
            if evidence_id not in values:
                values.append(evidence_id)
    return values


def _prediction_contexts(
    hits: Iterable[SearchHit], resolver: LocatorResolver
) -> list[dict[str, Any]]:
    return [
        {
            "context_id": hit.chunk_id,
            "evidence_ids": resolver.evidence_ids(hit),
        }
        for hit in hits
    ]


def _select_threshold(cases: list[dict[str, Any]], scores: dict[str, float]) -> tuple[float, float]:
    development = [case for case in cases if case["split"] == "dev"]
    ranked: list[tuple[float, float]] = []
    for threshold in THRESHOLD_GRID:
        correct = sum(
            (scores.get(case["case_id"], 0.0) < threshold) == (not case["answerable"])
            for case in development
        )
        ranked.append((correct / len(development), threshold))
    accuracy, threshold = max(ranked, key=lambda item: (item[0], item[1]))
    return threshold, accuracy


def _evaluation_cases(cases: list[dict[str, Any]], stage: str) -> list[dict[str, Any]]:
    if stage == "all":
        return cases
    split = "dev" if stage == "dev" else "test"
    selected = [case for case in cases if case["split"] == split]
    if not selected:
        raise ValueError(f"evaluation stage {stage!r} contains no cases")
    return selected


def _load_development_lock(
    path: Path,
    corpus_hash: str,
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    try:
        report = _read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("development evaluation lock is unavailable") from error
    if (
        report.get("status") != "dev_passed"
        or report.get("stage") != "dev"
        or report.get("evidence_status") != "VERIFIED_SYNTHETIC"
        or report.get("corpus_hash") != corpus_hash
    ):
        raise ValueError("development evaluation lock is not a passed matching dev report")
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("development evaluation lock has no metrics")
    gates = metrics.get("gates")
    if not isinstance(gates, dict) or not (
        gates.get("generation_structure_failures_is_zero") is True
        and gates.get("generation_request_failures_is_zero") is True
        and gates.get("server_citation_id_validity_is_100_percent") is True
    ):
        raise ValueError("development generation gates are not passed")
    threshold_block = metrics.get("refusal_threshold")
    if not isinstance(threshold_block, dict) or threshold_block.get("selected_on") != "dev":
        raise ValueError("development evaluation lock has no dev threshold")
    by_variant = threshold_block.get("by_variant")
    if not isinstance(by_variant, dict):
        raise ValueError("development evaluation lock has invalid thresholds")
    thresholds: dict[str, dict[str, float]] = {}
    for variant in VARIANTS:
        value = by_variant.get(variant.value)
        if not isinstance(value, dict):
            raise ValueError("development evaluation lock is missing a variant threshold")
        threshold = value.get("value")
        accuracy = value.get("development_accuracy")
        if (
            type(threshold) not in (int, float)
            or not 0.0 <= float(threshold) <= 1.0
            or type(accuracy) not in (int, float)
        ):
            raise ValueError("development evaluation lock has invalid threshold values")
        thresholds[variant.value] = {
            "value": float(threshold),
            "development_accuracy": float(accuracy),
        }
    return thresholds, report


def _split_metrics(
    cases: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    facts: list[dict[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    predictions_by_id = {prediction["case_id"]: prediction for prediction in predictions}
    for split in ("dev", "test", "all"):
        selected_cases = (
            cases if split == "all" else [case for case in cases if case["split"] == split]
        )
        selected_predictions = [predictions_by_id[case["case_id"]] for case in selected_cases]
        result[split] = evaluate_records(selected_cases, selected_predictions, facts)["metrics"]
    return result


def _false_answer_rate(
    cases: list[dict[str, Any]], predictions: list[dict[str, Any]], split: str
) -> float:
    prediction_by_id = {prediction["case_id"]: prediction for prediction in predictions}
    unanswerable = [case for case in cases if not case["answerable"] and case["split"] == split]
    if not unanswerable:
        return 0.0
    false_answers = sum(not prediction_by_id[case["case_id"]]["refused"] for case in unanswerable)
    return false_answers / len(unanswerable)


def run(args: argparse.Namespace) -> dict[str, Any]:
    dataset_validation = validate_repository(REPOSITORY_ROOT)
    corpus_hash = _corpus_hash()
    corpus_manifest = _read_json(
        REPOSITORY_ROOT / "datasets" / "generated" / "corpus_manifest.json"
    )
    all_cases = load_jsonl(REPOSITORY_ROOT / "evaluation" / "frozen_cases.jsonl")
    facts = load_jsonl(REPOSITORY_ROOT / "datasets" / "generated" / "canonical_facts.jsonl")
    resolver = LocatorResolver(corpus_manifest)
    stage = getattr(args, "stage", "all")
    if stage not in {"all", "dev", "frozen"}:
        raise ValueError(f"unsupported evaluation stage: {stage!r}")
    cases = _evaluation_cases(all_cases, stage)
    development_lock: dict[str, Any] | None = None
    locked_thresholds: dict[str, dict[str, float]] | None = None
    if stage == "frozen":
        development_report = Path(
            getattr(
                args,
                "development_report",
                REPOSITORY_ROOT / "artifacts" / "evaluation" / "dev.json",
            )
        )
        locked_thresholds, development_lock = _load_development_lock(
            development_report,
            corpus_hash,
        )

    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    if args.provider == "dashscope" and not api_key:
        raise SystemExit(
            "DASHSCOPE_API_KEY is required for VERIFIED_SYNTHETIC evaluation; "
            "the runner will not substitute OfflineProvider."
        )

    with (
        tempfile.TemporaryDirectory(prefix="cvrag-evaluation-") as database_directory,
        ExitStack() as cleanup,
    ):
        settings = Settings(
            DASHSCOPE_API_KEY=api_key,
            CVRAG_PROVIDER=args.provider,
            CVRAG_MODEL_DIR=args.model_dir.resolve(),
            ELASTICSEARCH_URL=args.elasticsearch_url,
            CVRAG_INDEX_NAME=f"cvrag-eval-{corpus_hash[:12]}",
            CVRAG_DATABASE_URL=f"sqlite:///{Path(database_directory) / 'evaluation.db'}",
        )
        if development_lock is not None and locked_thresholds is not None:
            locked_config = development_lock.get("runtime_config")
            locked_hash = development_lock.get("config_hash")
            expected_config = runtime_evaluation_config(
                settings,
                locked_thresholds[FINAL_VARIANT.value]["value"],
            )
            if locked_config != expected_config or locked_hash != runtime_config_hash(
                expected_config
            ):
                raise ValueError("development evaluation lock runtime configuration drifted")
        provider = CachedProvider(build_provider(settings))
        cleanup.callback(provider.close)
        evaluation_evidence_status = (
            "VERIFIED_SYNTHETIC" if args.provider == "dashscope" else "IMPLEMENTED_OFFLINE_VERIFIED"
        )
        index = build_elasticsearch_index(settings)
        cleanup.callback(index.client.close)
        index.rebuild()
        repository = DocumentRepository(settings.database_path)
        repository.initialize()
        document_service = DocumentService(settings, repository, index, provider)
        retrieval_service = RetrievalService(index, provider)

        parser_results: list[dict[str, Any]] = []
        for document in corpus_manifest["documents"]:
            artifact_path = REPOSITORY_ROOT / document["artifact"]["path"]
            started = time.perf_counter()
            try:
                response = document_service.ingest(document["filename"], artifact_path.read_bytes())
                parser_results.append(
                    {
                        "filename": document["filename"],
                        "status": "ready",
                        "parser_version": response.document.parser_version,
                        "chunk_count": response.document.chunk_count,
                        "latency_ms": (time.perf_counter() - started) * 1000,
                    }
                )
            except Exception as error:
                parser_results.append(
                    {
                        "filename": document["filename"],
                        "status": "failed",
                        "error_code": getattr(error, "code", "ingestion_failed"),
                        "latency_ms": (time.perf_counter() - started) * 1000,
                    }
                )

        if any(result["status"] != "ready" for result in parser_results):
            failures = [result for result in parser_results if result["status"] != "ready"]
            return {
                "status": "failed",
                "evidence_status": "NOT_VERIFIED",
                "generated_at": datetime.now(UTC).isoformat(),
                "corpus_hash": corpus_hash,
                "dataset_validation": dataset_validation,
                "metrics": {
                    "parser_success_rate": (len(parser_results) - len(failures))
                    / len(parser_results)
                },
                "badcases": [
                    {
                        "category": "Parser",
                        "summary": "Document ingestion did not reach READY.",
                        **failure,
                    }
                    for failure in failures
                ],
                "limitations": ["Retrieval evaluation stopped because ingestion was incomplete."],
            }

        try:
            locator_ledger = _runtime_locator_ledger(
                index.all_candidates(),
                resolver,
                facts,
            )
        except LocatorResolutionError as error:
            return {
                "status": "failed",
                "evidence_status": "NOT_VERIFIED",
                "generated_at": datetime.now(UTC).isoformat(),
                "corpus_hash": corpus_hash,
                "dataset_validation": dataset_validation,
                "metrics": {
                    "parser_success_rate": 1.0,
                    "parser_documents": parser_results,
                },
                "badcases": [
                    {
                        "category": "Parser",
                        "summary": str(error),
                    }
                ],
                "limitations": [
                    "Retrieval evaluation stopped because a runtime locator was ambiguous."
                ],
            }
        if locator_ledger["missing_evidence"]:
            return {
                "status": "failed",
                "evidence_status": "NOT_VERIFIED",
                "generated_at": datetime.now(UTC).isoformat(),
                "corpus_hash": corpus_hash,
                "dataset_validation": dataset_validation,
                "metrics": {
                    "parser_success_rate": 1.0,
                    "parser_documents": parser_results,
                    "locator_ledger": locator_ledger,
                },
                "badcases": [
                    {
                        "category": "Parser",
                        "summary": "Frozen evidence has no exact runtime locator.",
                        "missing_evidence": locator_ledger["missing_evidence"],
                    }
                ],
                "limitations": [
                    "Retrieval evaluation stopped before scoring because locator "
                    "coverage was incomplete."
                ],
            }

        raw_results: dict[str, list[dict[str, Any]]] = {variant.value: [] for variant in VARIANTS}
        score_by_variant: dict[str, dict[str, float]] = {variant.value: {} for variant in VARIANTS}
        response_by_case: dict[str, Any] = {}
        for variant in VARIANTS:
            for case in cases:
                started = time.perf_counter()
                response = retrieval_service.search(
                    RetrievalRequest(query=case["question"], variant=variant, top_k=5)
                )
                latency_ms = (time.perf_counter() - started) * 1000
                prediction = {
                    "case_id": case["case_id"],
                    "retrieved": [
                        {"evidence_id": evidence_id}
                        for evidence_id in _prediction_ids(response.hits, resolver)
                    ],
                    "citation_ids": [],
                    "retrieved_contexts": _prediction_contexts(response.hits, resolver),
                    "offered_citation_ids": [],
                    "generated_citation_ids": [],
                    "refused": False,
                    "latency_ms": latency_ms,
                    "degraded": response.degraded,
                    "degradation_codes": response.degradation_codes,
                    "top_score": response.hits[0].score if response.hits else 0.0,
                }
                raw_results[variant.value].append(prediction)
                score_by_variant[variant.value][case["case_id"]] = prediction["top_score"]
                if variant == FINAL_VARIANT:
                    response_by_case[case["case_id"]] = response

        rerank_variants = {
            RetrievalVariant.HYBRID_RRF_RERANK.value,
            RetrievalVariant.ROUTED_HYBRID_RRF_RERANK.value,
        }
        degraded_rerank = [
            {
                "category": "Rerank",
                "case_id": prediction["case_id"],
                "variant": variant_name,
                "reason": ",".join(prediction["degradation_codes"]),
            }
            for variant_name, predictions in raw_results.items()
            if variant_name in rerank_variants
            for prediction in predictions
            if prediction["degraded"]
        ]
        if degraded_rerank:
            return {
                "status": "failed",
                "evidence_status": "NOT_VERIFIED",
                "generated_at": datetime.now(UTC).isoformat(),
                "corpus_hash": corpus_hash,
                "dataset_validation": dataset_validation,
                "metrics": {
                    "parser_success_rate": 1.0,
                    "parser_documents": parser_results,
                    "locator_ledger": locator_ledger,
                },
                "badcases": degraded_rerank,
                "limitations": [
                    "Rerank degradation is fail-closed and no effectiveness metrics were accepted."
                ],
            }

        thresholds: dict[str, dict[str, float]] = locked_thresholds or {}
        if not thresholds:
            for variant in VARIANTS:
                value, accuracy = _select_threshold(cases, score_by_variant[variant.value])
                thresholds[variant.value] = {
                    "value": value,
                    "development_accuracy": accuracy,
                }
        for variant in VARIANTS:
            for prediction in raw_results[variant.value]:
                prediction["refused"] = prediction["top_score"] < thresholds[variant.value]["value"]
                prediction["threshold"] = thresholds[variant.value]["value"]
        threshold = thresholds[FINAL_VARIANT.value]["value"]
        generation_latencies: list[float] = []
        final_by_case = {
            prediction["case_id"]: prediction for prediction in raw_results[FINAL_VARIANT.value]
        }
        for case in cases:
            case_id = case["case_id"]
            prediction = final_by_case[case_id]
            response = response_by_case[case_id]
            refused = not response.hits or prediction["top_score"] < threshold
            prediction["refused"] = refused
            prediction["threshold"] = threshold
            if refused:
                prediction["outcome"] = "insufficient_evidence"
                continue
            sources = [
                CitationSource(citation_id=f"S{index_value}", hit=hit)
                for index_value, hit in enumerate(response.hits, start=1)
            ]
            source_by_id = {source.citation_id: source for source in sources}
            prediction["offered_citation_ids"] = list(source_by_id)
            generation_started = time.perf_counter()
            try:
                generated = provider.generate(case["question"], sources)
            except ProviderError as error:
                prediction["outcome"] = "needs_review"
                prediction["generation_error"] = error.code
                prediction["generation_error_category"] = error.diagnostic_category
                prediction["generation_retry_attempted"] = error.retry_attempted
                continue
            finally:
                generation_latencies.append((time.perf_counter() - generation_started) * 1000)
            if not generated.answerable:
                prediction["refused"] = True
                prediction["outcome"] = "insufficient_evidence"
                continue
            citations: list[str] = []
            generated_citation_ids: list[str] = []
            invalid = False
            for claim in generated.claims:
                for citation_id in claim.citation_ids:
                    generated_citation_ids.append(citation_id)
                    source = source_by_id.get(citation_id)
                    if source is None:
                        invalid = True
                        citations.append(f"invalid:{citation_id}")
                        continue
                    for evidence_id in resolver.evidence_ids(source.hit):
                        if evidence_id not in citations:
                            citations.append(evidence_id)
            prediction["citation_ids"] = citations
            prediction["generated_citation_ids"] = generated_citation_ids
            prediction["outcome"] = "needs_review" if invalid else "answered"

        variant_metrics = {
            variant.value: _split_metrics(cases, raw_results[variant.value], facts)
            for variant in VARIANTS
        }
        server_citation_id_validity = variant_metrics[FINAL_VARIANT.value]["all"][
            "server_citation_id_validity"
        ]
        metric_split = "dev" if stage == "dev" else "test"
        final_test = variant_metrics[FINAL_VARIANT.value][metric_split]
        baseline_test = variant_metrics[RetrievalVariant.BM25.value][metric_split]
        final_false_answer_rate = _false_answer_rate(
            cases, raw_results[FINAL_VARIANT.value], metric_split
        )
        baseline_false_answer_rate = _false_answer_rate(
            cases, raw_results[RetrievalVariant.BM25.value], metric_split
        )
        final_predictions = raw_results[FINAL_VARIANT.value]
        generation_structure_failures = sum(
            prediction.get("generation_error") == "generation_invalid_structure"
            for prediction in final_predictions
        )
        generation_request_failures = sum(
            prediction.get("outcome") == "needs_review" for prediction in final_predictions
        )
        gates = {
            "generation_structure_failures_is_zero": generation_structure_failures == 0,
            "generation_request_failures_is_zero": generation_request_failures == 0,
            "server_citation_id_validity_is_100_percent": (server_citation_id_validity == 1.0),
        }
        if stage != "dev":
            gates = {
                "recall_at_5_not_below_bm25": (
                    final_test["evidence_recall_at_5"] >= baseline_test["evidence_recall_at_5"]
                ),
                "ndcg_at_5_above_bm25": (final_test["ndcg_at_5"] > baseline_test["ndcg_at_5"]),
                "unanswerable_false_answer_rate_not_worse": (
                    final_false_answer_rate <= baseline_false_answer_rate
                ),
                **gates,
            }

        badcases: list[dict[str, Any]] = []
        case_by_id = {case["case_id"]: case for case in cases}
        for prediction in raw_results[FINAL_VARIANT.value]:
            case = case_by_id[prediction["case_id"]]
            required = {item["evidence_id"] for item in case["gold"]["required_evidence"]}
            retrieved = {item["evidence_id"] for item in prediction["retrieved"][:5]}
            if case["answerable"] and not required.issubset(retrieved):
                badcases.append(
                    {
                        "category": "RetrievalPool",
                        "case_id": case["case_id"],
                        "missing_evidence": sorted(required - retrieved),
                    }
                )
            if prediction.get("outcome") == "needs_review":
                badcases.append(
                    {
                        "category": "Citation",
                        "case_id": case["case_id"],
                        "reason": prediction.get("generation_error", "invalid_citation"),
                        "diagnostic_category": prediction.get("generation_error_category"),
                        "retry_attempted": prediction.get("generation_retry_attempted", False),
                    }
                )
        hybrid_by_id = {
            prediction["case_id"]: prediction
            for prediction in raw_results[RetrievalVariant.HYBRID_RRF.value]
        }
        reranked_by_id = {
            prediction["case_id"]: prediction
            for prediction in raw_results[RetrievalVariant.HYBRID_RRF_RERANK.value]
        }
        for case in cases:
            required = {item["evidence_id"] for item in case["gold"]["required_evidence"]}
            if not required:
                continue
            before = {
                item["evidence_id"] for item in hybrid_by_id[case["case_id"]]["retrieved"][:5]
            }
            after = {
                item["evidence_id"] for item in reranked_by_id[case["case_id"]]["retrieved"][:5]
            }
            if required.issubset(before) and not required.issubset(after):
                badcases.append(
                    {
                        "category": "Rerank",
                        "case_id": case["case_id"],
                        "missing_after_rerank": sorted(required - after),
                    }
                )

        if evaluation_evidence_status == "VERIFIED_SYNTHETIC":
            if stage == "dev":
                report_status = "dev_passed" if all(gates.values()) else "failed"
            else:
                report_status = "passed" if all(gates.values()) else "failed"
        else:
            report_status = "contract_only"
        runtime_config = runtime_evaluation_config(settings, threshold)
        report = {
            "status": report_status,
            "stage": stage,
            "evidence_status": evaluation_evidence_status,
            "generated_at": datetime.now(UTC).isoformat(),
            "corpus_hash": corpus_hash,
            "config_hash": runtime_config_hash(runtime_config),
            "runtime_config": runtime_config,
            "dataset_validation": dataset_validation,
            "metrics": {
                "parser_success_rate": 1.0,
                "parser_documents": parser_results,
                "locator_ledger": locator_ledger,
                "variants": variant_metrics,
                "refusal_threshold": {
                    "selected_on": "dev",
                    "grid": list(THRESHOLD_GRID),
                    "by_variant": thresholds,
                },
                "server_citation_id_validity": server_citation_id_validity,
                "generation_validation": {
                    "structure_failures": generation_structure_failures,
                    "request_failures": generation_request_failures,
                },
                "generation_latency_ms": {
                    "samples": len(generation_latencies),
                    "p50": sorted(generation_latencies)[len(generation_latencies) // 2]
                    if generation_latencies
                    else 0.0,
                    "p95": sorted(generation_latencies)[
                        min(len(generation_latencies) - 1, int(len(generation_latencies) * 0.95))
                    ]
                    if generation_latencies
                    else 0.0,
                },
                "gates": gates,
            },
            "badcases": badcases,
            "limitations": [
                "VERIFIED_SYNTHETIC covers only the committed public fixtures.",
                (
                    "It does not establish real-standard coverage, laboratory accreditation, "
                    "user-trial evidence, or production effectiveness."
                ),
                (
                    "Citation precision and recall use exact frozen evidence identities; no "
                    "automatic claim-support metric is labeled as human review."
                ),
            ],
        }

        args.predictions_dir.mkdir(parents=True, exist_ok=True)
        for variant in VARIANTS:
            output = args.predictions_dir / f"{variant.value}.jsonl"
            output.write_text(
                "".join(
                    json.dumps(prediction, ensure_ascii=False, sort_keys=True) + "\n"
                    for prediction in raw_results[variant.value]
                ),
                encoding="utf-8",
                newline="\n",
            )
        return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the frozen CVRAG ingestion/retrieval/citation evaluation."
    )
    parser.add_argument("--provider", choices=("dashscope", "offline"), default="dashscope")
    parser.add_argument("--stage", choices=("all", "dev", "frozen"), default="all")
    parser.add_argument("--model-dir", type=Path, default=REPOSITORY_ROOT / "models" / "deepdoc")
    parser.add_argument("--elasticsearch-url", default="http://localhost:9200")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "artifacts" / "evaluation" / "latest.json",
    )
    parser.add_argument(
        "--predictions-dir",
        type=Path,
        default=REPOSITORY_ROOT / "artifacts" / "evaluation" / "predictions",
    )
    parser.add_argument(
        "--development-report",
        type=Path,
        default=REPOSITORY_ROOT / "artifacts" / "evaluation" / "dev.json",
        help="Passed dev report required when --stage frozen.",
    )
    args = parser.parse_args()
    report = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["status"] in {"passed", "contract_only", "dev_passed"} else 1)


if __name__ == "__main__":
    main()
