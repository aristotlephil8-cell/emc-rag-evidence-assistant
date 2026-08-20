from __future__ import annotations

import hashlib
import json
from typing import Any

from app.chunking import CHUNKER_VERSION, MAX_CHUNK_TOKENS
from app.config import Settings
from app.ingestion.versions import PARSER_BUNDLE_VERSION
from app.search import INITIAL_LIMIT, RERANK_LIMIT, RRF_K, QueryRouter

MAPPING_VERSION = "mapping-v4-table-locator"
RUNTIME_CONFIG_SCHEMA_VERSION = 1


def pipeline_index_version(settings: Settings) -> str:
    material = "|".join(
        [
            MAPPING_VERSION,
            PARSER_BUNDLE_VERSION,
            CHUNKER_VERSION,
            settings.EMBEDDING_MODEL,
            str(settings.EMBEDDING_DIMENSION),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def runtime_evaluation_config(
    settings: Settings,
    refusal_threshold: float,
) -> dict[str, Any]:
    exact_lexical, exact_vector = QueryRouter.weights("exact")
    semantic_lexical, semantic_vector = QueryRouter.weights("semantic")
    return {
        "schema_version": RUNTIME_CONFIG_SCHEMA_VERSION,
        "provider": "dashscope",
        "models": {
            "embedding": settings.EMBEDDING_MODEL,
            "embedding_dimension": settings.EMBEDDING_DIMENSION,
            "rerank": settings.RERANK_MODEL,
            "generation": settings.GENERATION_MODEL,
        },
        "pipeline": {
            "parser_bundle": PARSER_BUNDLE_VERSION,
            "chunker": CHUNKER_VERSION,
            "maximum_chunk_tokens": MAX_CHUNK_TOKENS,
            "index_version": pipeline_index_version(settings),
        },
        "retrieval": {
            "variant": "routed_hybrid_rrf_rerank",
            "bm25_limit": INITIAL_LIMIT,
            "vector_limit": INITIAL_LIMIT,
            "rrf_k": RRF_K,
            "rerank_limit": RERANK_LIMIT,
            "top_k": 5,
            "bm25_confidence": "score/(score+1)",
            "vector_confidence": "clamp_elasticsearch_score_0_1",
            "exact_weights": {
                "lexical": exact_lexical,
                "vector": exact_vector,
            },
            "semantic_weights": {
                "lexical": semantic_lexical,
                "vector": semantic_vector,
            },
        },
        "refusal_threshold": refusal_threshold,
    }


def runtime_config_hash(config: dict[str, Any]) -> str:
    encoded = json.dumps(
        config,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def locked_refusal_threshold(settings: Settings) -> float:
    if settings.CVRAG_PROVIDER != "dashscope" or not settings.CVRAG_REQUIRE_EVALUATION_LOCK:
        return settings.CVRAG_REFUSAL_THRESHOLD

    try:
        report = json.loads(settings.CVRAG_EVALUATION_REPORT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("evaluation_lock_unavailable") from error
    if not isinstance(report, dict):
        raise RuntimeError("evaluation_lock_invalid")
    if report.get("status") != "passed" or report.get("evidence_status") != "VERIFIED_SYNTHETIC":
        raise RuntimeError("evaluation_lock_not_passed")

    locked_config = report.get("runtime_config")
    locked_hash = report.get("config_hash")
    if not isinstance(locked_config, dict) or not isinstance(locked_hash, str):
        raise RuntimeError("evaluation_lock_invalid")
    threshold = locked_config.get("refusal_threshold")
    if type(threshold) not in (int, float) or not 0.0 <= float(threshold) <= 1.0:
        raise RuntimeError("evaluation_lock_invalid_threshold")

    expected = runtime_evaluation_config(settings, float(threshold))
    if locked_config != expected or runtime_config_hash(expected) != locked_hash:
        raise RuntimeError("evaluation_lock_config_mismatch")
    return float(threshold)


__all__ = [
    "MAPPING_VERSION",
    "locked_refusal_threshold",
    "pipeline_index_version",
    "runtime_config_hash",
    "runtime_evaluation_config",
]
