"""Content-free metrics for the ignored local-private evaluation protocol."""

from __future__ import annotations

import math
from collections.abc import Mapping
from statistics import median
from typing import Any

from app.models import RetrievalResponse, RetrievalVariant
from app.private_rag import PrivateEvaluationCase
from app.search import RRF_K
from app.services import ChatOutcome

PRIVATE_THRESHOLD_GRID = tuple(round(index * 0.05, 2) for index in range(21))
PRIVATE_RERANK_RRF_WEIGHT_GRID = (0.25, 0.5, 0.75, 1.0)


def select_private_refusal_threshold(
    cases: tuple[PrivateEvaluationCase, ...],
    top_scores: Mapping[str, float],
) -> tuple[float, float]:
    """Choose the refusal threshold on development cases only."""

    if not cases:
        raise ValueError("private development cases are required")
    ranked: list[tuple[float, float]] = []
    for threshold in PRIVATE_THRESHOLD_GRID:
        correct = sum(
            (top_scores.get(case.case_id, 0.0) < threshold) == (not case.answerable)
            for case in cases
        )
        ranked.append((correct / len(cases), threshold))
    accuracy, threshold = max(ranked, key=lambda item: (item[0], item[1]))
    return threshold, accuracy


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[rank]


def private_retrieval_metrics(
    cases: tuple[PrivateEvaluationCase, ...],
    retrievals: Mapping[str, RetrievalResponse],
) -> dict[str, Any]:
    """Aggregate page-locator retrieval metrics without semantic-support claims."""

    answerable = [case for case in cases if case.answerable]
    evidence_hits = 0
    reciprocal_ranks = 0.0
    normalized_dcg = 0.0
    context_precision = 0.0
    rerank_degradations = 0
    latencies: list[float] = []

    for case in cases:
        retrieval = retrievals[case.case_id]
        latencies.append(retrieval.latency_ms)
        rerank_degradations += int(retrieval.degraded)
        expected_pages = set(case.evidence_pages)
        if not expected_pages:
            continue
        relevant_ranks = [
            rank
            for rank, hit in enumerate(retrieval.hits[:10], start=1)
            if hit.source.page_number in expected_pages
        ]
        relevant_top_five = [rank for rank in relevant_ranks if rank <= 5]
        if relevant_top_five:
            evidence_hits += 1
        if relevant_ranks:
            reciprocal_ranks += 1.0 / relevant_ranks[0]
        dcg = sum(1.0 / math.log2(rank + 1) for rank in relevant_top_five)
        normalized_dcg += min(1.0, dcg)
        context_precision += len(relevant_top_five) / min(5, len(retrieval.hits) or 1)

    answerable_count = len(answerable)
    return {
        "case_count": len(cases),
        "answerable_cases": answerable_count,
        "evidence_recall_at_5": evidence_hits / answerable_count if answerable_count else 1.0,
        "hit_at_5": evidence_hits / answerable_count if answerable_count else 1.0,
        "mrr_at_10": reciprocal_ranks / answerable_count if answerable_count else 1.0,
        "ndcg_at_5": normalized_dcg / answerable_count if answerable_count else 1.0,
        "context_precision_at_5": context_precision / answerable_count if answerable_count else 1.0,
        "rerank_degradations": rerank_degradations,
        "latency_ms": {
            "p50": median(latencies) if latencies else 0.0,
            "p95": _percentile(latencies, 0.95),
        },
    }


def blend_rerank_with_rrf(
    response: RetrievalResponse,
    rerank_weight: float,
    *,
    top_k: int,
) -> RetrievalResponse:
    """Keep RRF signal when a reranker makes the final ordering.

    This is deliberately local-private tuning support. It operates only on the
    already returned candidate set and never emits private evidence text.
    """

    if not 0.0 <= rerank_weight <= 1.0 or top_k < 1:
        raise ValueError("invalid private rerank blend")
    if (
        response.variant
        not in {
            RetrievalVariant.HYBRID_RRF_RERANK,
            RetrievalVariant.ROUTED_HYBRID_RRF_RERANK,
        }
        or response.degraded
    ):
        return response.model_copy(update={"hits": response.hits[:top_k]})
    rescored = []
    for hit in response.hits:
        rerank = hit.scores.rerank
        rrf = hit.scores.rrf
        if rerank is None or rrf is None:
            return response.model_copy(update={"hits": response.hits[:top_k]})
        normalized_rrf = min(1.0, max(0.0, rrf * (RRF_K + 1)))
        score = rerank_weight * rerank + (1.0 - rerank_weight) * normalized_rrf
        rescored.append((hit, score))
    ordered = sorted(
        rescored,
        key=lambda item: (-item[1], -(item[0].scores.rrf or 0.0), item[0].chunk_id),
    )
    return response.model_copy(
        update={
            "hits": [
                hit.model_copy(update={"rank": rank, "score": score})
                for rank, (hit, score) in enumerate(ordered[:top_k], start=1)
            ]
        }
    )


def select_private_rerank_weight(
    cases: tuple[PrivateEvaluationCase, ...],
    bm25_retrievals: Mapping[str, RetrievalResponse],
    reranked_retrievals: Mapping[str, RetrievalResponse],
) -> tuple[float, dict[str, RetrievalResponse], dict[str, Any]]:
    """Select only a positive rerank blend that clears the dev effect floor."""

    bm25_metrics = private_retrieval_metrics(cases, bm25_retrievals)
    eligible: list[tuple[float, dict[str, RetrievalResponse], dict[str, Any]]] = []
    for weight in PRIVATE_RERANK_RRF_WEIGHT_GRID:
        blended = {
            case_id: blend_rerank_with_rrf(response, weight, top_k=10)
            for case_id, response in reranked_retrievals.items()
        }
        metrics = private_retrieval_metrics(cases, blended)
        if (
            metrics["evidence_recall_at_5"] >= bm25_metrics["evidence_recall_at_5"]
            and metrics["ndcg_at_5"] > bm25_metrics["ndcg_at_5"]
            and metrics["rerank_degradations"] == 0
        ):
            eligible.append((weight, blended, metrics))
    if not eligible:
        raise ValueError("private_effect_gate_not_met_on_dev")
    return max(
        eligible,
        key=lambda item: (
            item[2]["ndcg_at_5"],
            item[2]["evidence_recall_at_5"],
            item[2]["mrr_at_10"],
            item[0],
        ),
    )


def private_metrics(
    cases: tuple[PrivateEvaluationCase, ...],
    retrievals: Mapping[str, RetrievalResponse],
    outcomes: Mapping[str, ChatOutcome],
    threshold: float,
) -> dict[str, Any]:
    """Aggregate only numeric, page-grounded, server-validator metrics.

    This intentionally does not claim semantic claim support: page evidence
    is a locator check, and cited IDs are checked by :class:`ChatService`.
    """

    retrieval_metrics = private_retrieval_metrics(cases, retrievals)
    answerable = [case for case in cases if case.answerable]
    unanswerable = [case for case in cases if not case.answerable]
    refusal_correct = 0
    false_answers = 0
    rerank_degradations = 0
    generation_structure_failures = 0
    generation_request_failures = 0
    citation_attempts = 0
    valid_citation_answers = 0
    latencies: list[float] = []

    for case in cases:
        retrieval = retrievals[case.case_id]
        outcome = outcomes[case.case_id]
        latencies.append(retrieval.latency_ms)
        rerank_degradations += int(retrieval.degraded)
        refused = outcome.status == "insufficient_evidence"
        refusal_correct += int(refused == (not case.answerable))
        false_answers += int(not case.answerable and not refused)
        if outcome.status == "needs_review":
            if outcome.error_code == "generation_invalid_structure":
                generation_structure_failures += 1
            elif outcome.error_code not in {"retrieval_degraded", "invalid_citation"}:
                generation_request_failures += 1
        if outcome.status == "answered":
            citation_attempts += 1
            # ChatService returns this state only after its citation allow-list
            # validation has accepted every factual claim.
            valid_citation_answers += 1
        elif outcome.error_code == "invalid_citation":
            citation_attempts += 1

    citation_validity = valid_citation_answers / citation_attempts if citation_attempts else 1.0
    false_answer_rate = false_answers / len(unanswerable) if unanswerable else 0.0
    return {
        **retrieval_metrics,
        "answerable_cases": len(answerable),
        "unanswerable_cases": len(unanswerable),
        "refusal_accuracy": refusal_correct / len(cases),
        "unanswerable_false_answer_rate": false_answer_rate,
        "server_citation_id_validity": citation_validity,
        "generation_structure_failures": generation_structure_failures,
        "generation_request_failures": generation_request_failures,
        "refusal_threshold": threshold,
        "latency_ms": {
            "p50": median(latencies) if latencies else 0.0,
            "p95": _percentile(latencies, 0.95),
        },
        "gates": {
            "rerank_degradation_is_zero": rerank_degradations == 0,
            "generation_structure_failures_is_zero": generation_structure_failures == 0,
            "generation_request_failures_is_zero": generation_request_failures == 0,
            "server_citation_id_validity_is_100_percent": citation_validity == 1.0,
        },
    }


def private_gates_pass(metrics: Mapping[str, Any]) -> bool:
    gates = metrics.get("gates")
    return isinstance(gates, dict) and all(value is True for value in gates.values())


__all__ = [
    "PRIVATE_THRESHOLD_GRID",
    "PRIVATE_RERANK_RRF_WEIGHT_GRID",
    "blend_rerank_with_rrf",
    "private_gates_pass",
    "private_metrics",
    "private_retrieval_metrics",
    "select_private_rerank_weight",
    "select_private_refusal_threshold",
]
