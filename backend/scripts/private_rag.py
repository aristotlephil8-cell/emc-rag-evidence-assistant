"""Private-only CVRAG PDF preparation and batch ingestion.

All source, OCR, chunks, SQLite records, and answer artifacts remain below
``datasets/private_local``. Terminal output is deliberately content-free.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elasticsearch import Elasticsearch

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.config import Settings  # noqa: E402
from app.db import DocumentRepository  # noqa: E402
from app.models import ChatRequest, DocumentStatus, RetrievalRequest, RetrievalVariant  # noqa: E402
from app.pipeline import (  # noqa: E402
    pipeline_index_version,
    runtime_config_hash,
    runtime_evaluation_config,
)
from app.private_evaluation import (  # noqa: E402
    blend_rerank_with_rrf,
    private_gates_pass,
    private_metrics,
    private_retrieval_metrics,
    select_private_refusal_threshold,
    select_private_rerank_weight,
)
from app.private_rag import (  # noqa: E402
    PRIVATE_EVIDENCE_STATUS,
    PrivateRagError,
    build_private_weak_cases,
    load_private_cases,
    load_private_parsed,
    parse_private_pdf,
    partition_private_parsed,
    prepare_private_pdf,
    private_input,
    private_root,
    private_summary,
)
from app.providers import build_provider  # noqa: E402
from app.search import ElasticsearchIndex, RetrievalService  # noqa: E402
from app.services import ChatService, DocumentService, IngestionLimits  # noqa: E402

PRIVATE_INGESTION_LIMITS = IngestionLimits(
    max_parsed_characters=5_000_000,
    max_parsed_sections=20_000,
    max_document_chunks=20_000,
)
PRIVATE_EVALUATION_CANDIDATES = 20


def _safe_error(code: str) -> dict[str, object]:
    return {
        "status": "failed",
        "evidence_status": PRIVATE_EVIDENCE_STATUS,
        "error_code": code,
    }


def _settings(
    args: argparse.Namespace,
    run_dir: Path,
    source_sha256: str,
    stage: str,
) -> Settings:
    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not api_key:
        raise PrivateRagError("private_provider_key_missing")
    index_name = f"cvrag-private-{source_sha256[:16]}-{stage}"
    return Settings(
        _env_file=None,
        DASHSCOPE_API_KEY=api_key,
        DASHSCOPE_BASE_URL=args.dashscope_base_url,
        DASHSCOPE_RERANK_URL=args.dashscope_rerank_url,
        CVRAG_PROVIDER="dashscope",
        CVRAG_MODEL_DIR=args.model_dir.resolve(),
        ELASTICSEARCH_URL=args.elasticsearch_url,
        CVRAG_INDEX_NAME=index_name,
        CVRAG_DATABASE_URL=f"sqlite:///{run_dir / 'state' / f'{stage}.db'}",
    )


def _write_private_report(run_dir: Path, name: str, value: dict[str, object]) -> None:
    path = run_dir / name
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _json_object(path: Path, unavailable_code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PrivateRagError(unavailable_code) from error
    if not isinstance(value, dict):
        raise PrivateRagError(unavailable_code)
    return value


def _runtime_config(
    settings: Settings,
    threshold: float,
    rerank_rrf_weight: float,
) -> dict[str, Any]:
    return {
        **runtime_evaluation_config(settings, threshold),
        "private_local": {"rerank_rrf_weight": rerank_rrf_weight},
    }


def _development_lock_path(run_dir: Path) -> Path:
    return run_dir / "dev_lock.json"


def _holdout_attempt_path(run_dir: Path) -> Path:
    return run_dir / "holdout_attempt.json"


def _load_development_lock(
    run_dir: Path,
    source_sha256: str,
    cases_sha256: str,
    settings: Settings,
    case_provenance: str,
) -> tuple[float, float, dict[str, Any]]:
    lock = _json_object(_development_lock_path(run_dir), "private_development_lock_unavailable")
    threshold = lock.get("refusal_threshold")
    config = lock.get("runtime_config")
    config_hash = lock.get("config_hash")
    rerank_rrf_weight = lock.get("rerank_rrf_weight")
    if (
        lock.get("status") != "dev_passed"
        or lock.get("evidence_status") != PRIVATE_EVIDENCE_STATUS
        or lock.get("source_sha256") != source_sha256
        or lock.get("cases_sha256") != cases_sha256
        or lock.get("case_provenance") != case_provenance
        or type(threshold) not in (int, float)
        or not 0.0 <= float(threshold) <= 1.0
        or type(rerank_rrf_weight) not in (int, float)
        or not 0.0 < float(rerank_rrf_weight) <= 1.0
        or not isinstance(config, dict)
        or not isinstance(config_hash, str)
    ):
        raise PrivateRagError("private_development_lock_invalid")
    expected = _runtime_config(settings, float(threshold), float(rerank_rrf_weight))
    if config != expected or config_hash != runtime_config_hash(expected):
        raise PrivateRagError("private_development_config_drift")
    return float(threshold), float(rerank_rrf_weight), lock


def _require_stage(value: str) -> str:
    if value not in {"dev", "holdout"}:
        raise PrivateRagError("private_stage_invalid")
    return value


def _safe_terminal_result(value: dict[str, Any]) -> dict[str, Any]:
    """Keep terminal output free of private source identity and content."""

    result: dict[str, Any] = {
        "status": value.get("status", "failed"),
        "evidence_status": value.get("evidence_status", PRIVATE_EVIDENCE_STATUS),
    }
    for key in (
        "source_bytes",
        "page_count",
        "segment_count",
        "dev_pages",
        "holdout_pages",
        "parsed_sections",
        "chunk_count",
        "stage",
        "error_code",
        "case_provenance",
        "case_counts",
    ):
        if key in value:
            result[key] = value[key]
    metrics = value.get("metrics")
    if isinstance(metrics, dict):
        result["metrics"] = metrics
    return result


def _prepare(args: argparse.Namespace):
    root = private_root(args.private_root)
    source = private_input(args.input, root)
    run = prepare_private_pdf(source, root)
    try:
        parsed = (
            load_private_parsed(run)
            if run.parsed_path.exists()
            else parse_private_pdf(run, args.model_dir)
        )
    except PrivateRagError as error:
        _write_private_report(
            run.run_dir,
            "prepare_report.json",
            {
                **private_summary(run),
                "status": "failed",
                "evidence_status": PRIVATE_EVIDENCE_STATUS,
                "error_code": error.code,
                "generated_at": datetime.now(UTC).isoformat(),
            },
        )
        raise
    summary = private_summary(run, parsed)
    _write_private_report(
        run.run_dir,
        "prepare_report.json",
        {
            **summary,
            "generated_at": datetime.now(UTC).isoformat(),
            "parser_version": parsed.parser_version,
        },
    )
    return summary, run, parsed


def _ingest(args: argparse.Namespace) -> dict[str, object]:
    summary, run, parsed = _prepare(args)
    stage = _require_stage(args.stage)
    stage_parsed = partition_private_parsed(run, parsed, stage)
    cases_sha256: str | None = None
    if stage == "holdout":
        if args.cases is None:
            raise PrivateRagError("private_cases_required_for_holdout")
        holdout_cases, cases_sha256 = load_private_cases(args.cases, run.root, run, stage)
    settings = _settings(args, run.run_dir, run.source_sha256, stage)
    if stage == "holdout":
        assert cases_sha256 is not None
        _load_development_lock(
            run.run_dir,
            run.source_sha256,
            cases_sha256,
            settings,
            _single_case_provenance(holdout_cases),
        )
    provider = build_provider(settings)
    client = Elasticsearch(settings.ELASTICSEARCH_URL, request_timeout=30)
    index = ElasticsearchIndex(
        client=client,
        index_name=settings.CVRAG_INDEX_NAME,
        embedding_dimension=settings.EMBEDDING_DIMENSION,
        index_version=pipeline_index_version(settings),
    )
    try:
        index.rebuild()
        repository = DocumentRepository(settings.database_path)
        repository.initialize()
        service = DocumentService(
            settings,
            repository,
            index,
            provider,
            limits=PRIVATE_INGESTION_LIMITS,
        )
        source = private_input(args.input, run.root)
        response = service.ingest_preparsed(
            run.document_filename,
            source.read_bytes(),
            stage_parsed,
        )
        result = {
            **summary,
            "status": "ready",
            "evidence_status": PRIVATE_EVIDENCE_STATUS,
            "index_scope": "private_local_only",
            "stage": stage,
            "index_version": response.document.index_version,
            "chunk_count": response.document.chunk_count,
            "idempotent": response.idempotent,
            "warnings": len(response.warnings),
            "generated_at": datetime.now(UTC).isoformat(),
        }
        _write_private_report(run.run_dir, f"ingest_{stage}_report.json", result)
        return result
    finally:
        provider.close()
        client.close()


def _index_ready(repository: DocumentRepository, source_sha256: str) -> bool:
    record = repository.get(f"doc_{source_sha256[:24]}")
    return record is not None and record.status == DocumentStatus.READY


def _mark_holdout_attempt(
    run_dir: Path,
    *,
    source_sha256: str,
    cases_sha256: str,
    config_hash: str,
    case_provenance: str,
) -> None:
    path = _holdout_attempt_path(run_dir)
    if path.exists():
        raise PrivateRagError("private_holdout_already_attempted")
    _write_private_report(
        run_dir,
        "holdout_attempt.json",
        {
            "status": "started",
            "evidence_status": PRIVATE_EVIDENCE_STATUS,
            "source_sha256": source_sha256,
            "cases_sha256": cases_sha256,
            "config_hash": config_hash,
            "case_provenance": case_provenance,
            "generated_at": datetime.now(UTC).isoformat(),
        },
    )


def _single_case_provenance(cases: tuple[object, ...]) -> str:
    provenances = {getattr(case, "case_provenance", None) for case in cases}
    if len(provenances) != 1 or not isinstance(next(iter(provenances)), str):
        raise PrivateRagError("private_case_provenance_mixed")
    return next(iter(provenances))


class _PrivateBlendedRetrieval:
    """Private evaluation adapter that preserves RRF signal after reranking."""

    def __init__(self, retrieval: RetrievalService, rerank_rrf_weight: float):
        self._retrieval = retrieval
        self._rerank_rrf_weight = rerank_rrf_weight

    def search(self, request: RetrievalRequest):
        raw = self._retrieval.search(
            request.model_copy(update={"top_k": max(request.top_k, PRIVATE_EVALUATION_CANDIDATES)})
        )
        return blend_rerank_with_rrf(raw, self._rerank_rrf_weight, top_k=request.top_k)


def _build_cases(args: argparse.Namespace) -> dict[str, object]:
    summary, run, parsed = _prepare(args)
    _, manifest = build_private_weak_cases(run, parsed)
    return {
        **summary,
        "status": "frozen",
        "evidence_status": PRIVATE_EVIDENCE_STATUS,
        "case_provenance": manifest["case_provenance"],
        "case_counts": manifest["counts"],
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _evaluate(args: argparse.Namespace) -> dict[str, object]:
    summary, run, _ = _prepare(args)
    stage = _require_stage(args.stage)
    if args.cases is None:
        raise PrivateRagError("private_cases_required")
    cases, cases_sha256 = load_private_cases(args.cases, run.root, run, stage)
    case_provenance = _single_case_provenance(cases)
    settings = _settings(args, run.run_dir, run.source_sha256, stage)
    if stage == "holdout":
        threshold, rerank_rrf_weight, _ = _load_development_lock(
            run.run_dir,
            run.source_sha256,
            cases_sha256,
            settings,
            case_provenance,
        )
        _mark_holdout_attempt(
            run.run_dir,
            source_sha256=run.source_sha256,
            cases_sha256=cases_sha256,
            config_hash=runtime_config_hash(
                _runtime_config(settings, threshold, rerank_rrf_weight)
            ),
            case_provenance=case_provenance,
        )
    else:
        threshold = 0.0
        rerank_rrf_weight = 1.0

    provider = build_provider(settings)
    client = Elasticsearch(settings.ELASTICSEARCH_URL, request_timeout=30)
    index = ElasticsearchIndex(
        client=client,
        index_name=settings.CVRAG_INDEX_NAME,
        embedding_dimension=settings.EMBEDDING_DIMENSION,
        index_version=pipeline_index_version(settings),
    )
    try:
        repository = DocumentRepository(settings.database_path)
        repository.initialize()
        if not _index_ready(repository, run.source_sha256):
            raise PrivateRagError("private_stage_index_unavailable")
        retrieval = RetrievalService(index, provider)
        raw_retrievals_by_variant = {
            variant: {
                case.case_id: retrieval.search(
                    RetrievalRequest(
                        query=case.query,
                        variant=variant,
                        top_k=PRIVATE_EVALUATION_CANDIDATES,
                    )
                )
                for case in cases
            }
            for variant in RetrievalVariant
        }
        final_variant = RetrievalVariant.ROUTED_HYBRID_RRF_RERANK
        if stage == "dev":
            try:
                rerank_rrf_weight, retrievals, _ = select_private_rerank_weight(
                    cases,
                    raw_retrievals_by_variant[RetrievalVariant.BM25],
                    raw_retrievals_by_variant[final_variant],
                )
            except ValueError as error:
                raise PrivateRagError(str(error)) from error
        else:
            retrievals = {
                case_id: blend_rerank_with_rrf(response, rerank_rrf_weight, top_k=10)
                for case_id, response in raw_retrievals_by_variant[final_variant].items()
            }
        retrievals_by_variant = {
            variant: {
                case_id: (
                    blend_rerank_with_rrf(response, rerank_rrf_weight, top_k=10)
                    if variant
                    in {
                        RetrievalVariant.HYBRID_RRF_RERANK,
                        RetrievalVariant.ROUTED_HYBRID_RRF_RERANK,
                    }
                    else response.model_copy(update={"hits": response.hits[:10]})
                )
                for case_id, response in responses.items()
            }
            for variant, responses in raw_retrievals_by_variant.items()
        }
        retrievals_by_variant[final_variant] = retrievals
        if stage == "dev":
            threshold, development_accuracy = select_private_refusal_threshold(
                cases,
                {
                    case_id: response.hits[0].score if response.hits else 0.0
                    for case_id, response in retrievals.items()
                },
            )
        else:
            development_accuracy = None

        chat = ChatService(
            _PrivateBlendedRetrieval(retrieval, rerank_rrf_weight), provider, threshold
        )
        outcomes = {case.case_id: chat.answer(ChatRequest(query=case.query)) for case in cases}
        generation_metrics = private_metrics(cases, retrievals, outcomes, threshold)
        variant_metrics = {
            variant.value: private_retrieval_metrics(cases, responses)
            for variant, responses in retrievals_by_variant.items()
        }
        final_retrieval_metrics = variant_metrics[final_variant.value]
        bm25_metrics = variant_metrics[RetrievalVariant.BM25.value]
        effect_gates = {
            "routed_evidence_recall_at_5_not_lower_than_bm25": (
                final_retrieval_metrics["evidence_recall_at_5"]
                >= bm25_metrics["evidence_recall_at_5"]
            ),
            "routed_ndcg_at_5_higher_than_bm25": (
                final_retrieval_metrics["ndcg_at_5"] > bm25_metrics["ndcg_at_5"]
            ),
        }
        metrics = {
            "case_provenance": case_provenance,
            "variants": variant_metrics,
            "generation": generation_metrics,
            "gates": {**generation_metrics["gates"], **effect_gates},
        }
        config = _runtime_config(settings, threshold, rerank_rrf_weight)
        result: dict[str, object] = {
            **summary,
            "status": "dev_passed" if stage == "dev" and private_gates_pass(metrics) else "passed",
            "evidence_status": PRIVATE_EVIDENCE_STATUS,
            "stage": stage,
            "case_provenance": case_provenance,
            "rerank_rrf_weight": rerank_rrf_weight,
            "case_counts": {
                "total": len(cases),
                "answerable": sum(case.answerable for case in cases),
                "unanswerable": sum(not case.answerable for case in cases),
            },
            "cases_sha256": cases_sha256,
            "source_sha256": run.source_sha256,
            "runtime_config": config,
            "config_hash": runtime_config_hash(config),
            "metrics": metrics,
            "generated_at": datetime.now(UTC).isoformat(),
        }
        if stage == "dev":
            result["development_accuracy"] = development_accuracy
            if result["status"] != "dev_passed":
                result["status"] = "dev_failed"
            _write_private_report(run.run_dir, "dev_report.json", result)
            if result["status"] == "dev_passed":
                _write_private_report(
                    run.run_dir,
                    "dev_lock.json",
                    {
                        "status": "dev_passed",
                        "evidence_status": PRIVATE_EVIDENCE_STATUS,
                        "source_sha256": run.source_sha256,
                        "cases_sha256": cases_sha256,
                        "case_provenance": case_provenance,
                        "refusal_threshold": threshold,
                        "rerank_rrf_weight": rerank_rrf_weight,
                        "runtime_config": config,
                        "config_hash": result["config_hash"],
                        "generated_at": result["generated_at"],
                    },
                )
        else:
            if not private_gates_pass(metrics):
                result["status"] = "failed"
            _write_private_report(run.run_dir, "holdout_report.json", result)
        return result
    finally:
        provider.close()
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "build-cases", "ingest", "evaluate"))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument(
        "--private-root",
        type=Path,
        default=REPOSITORY_ROOT / "datasets" / "private_local",
    )
    parser.add_argument("--model-dir", type=Path, default=REPOSITORY_ROOT / "models" / "deepdoc")
    parser.add_argument("--elasticsearch-url", default="http://127.0.0.1:19200")
    parser.add_argument("--stage", choices=("dev", "holdout"), default="dev")
    parser.add_argument("--cases", type=Path)
    parser.add_argument(
        "--dashscope-base-url",
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    parser.add_argument(
        "--dashscope-rerank-url",
        default="https://dashscope.aliyuncs.com/compatible-api/v1/reranks",
    )
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            result = _prepare(args)[0]
        elif args.action == "build-cases":
            result = _build_cases(args)
        elif args.action == "ingest":
            result = _ingest(args)
        else:
            result = _evaluate(args)
    except PrivateRagError as error:
        result = _safe_error(error.code)
    except Exception:
        result = _safe_error("private_workflow_failed")
    print(json.dumps(_safe_terminal_result(result), ensure_ascii=False))
    raise SystemExit(0 if result["status"] in {"ready", "frozen", "dev_passed", "passed"} else 1)


if __name__ == "__main__":
    main()
