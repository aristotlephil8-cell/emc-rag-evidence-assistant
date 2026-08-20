"""Dependency-free retrieval, citation, refusal, and latency metrics.

Prediction JSONL contract (one row per frozen case)::

    {
      "case_id": "syn-q001",
      "retrieved": [{"evidence_id": "syn-ev-0001"}],
      "retrieved_contexts": [
        {"context_id": "chunk-1", "evidence_ids": ["syn-ev-0001"]}
      ],
      "citation_ids": ["syn-ev-0001"],
      "offered_citation_ids": ["S1"],
      "generated_citation_ids": ["S1"],
      "refused": false,
      "latency_ms": 42.5
    }

Rankings and citations are deduplicated by first occurrence.  Retrieval metrics
are evaluated on answerable cases; Evidence Recall@5 and citation metrics are
micro-averaged over stable evidence IDs.  Refusal accuracy covers every case.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable, Sequence


class EvaluationInputError(ValueError):
    """Raised when prediction or gold inputs violate the evaluation contract."""


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise EvaluationInputError(
                f"{path}:{line_number}: invalid JSON: {exc}"
            ) from exc
        if not isinstance(row, dict):
            raise EvaluationInputError(
                f"{path}:{line_number}: each JSONL row must be an object"
            )
        rows.append(row)
    return rows


def _stable_ids(items: Any, field: str = "evidence_id") -> list[str]:
    if items is None:
        return []
    if not isinstance(items, list):
        raise EvaluationInputError(f"expected a list, got {type(items).__name__}")
    values: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = item.get(field) if isinstance(item, dict) else item
        if not isinstance(value, str) or not value:
            raise EvaluationInputError(
                f"ranked/citation item must contain a non-empty {field}"
            )
        if value not in seen:
            values.append(value)
            seen.add(value)
    return values


def _retrieved_contexts(
    prediction: dict[str, Any], retrieved: list[str]
) -> list[dict[str, Any]]:
    raw_contexts = prediction.get("retrieved_contexts")
    if raw_contexts is None:
        return [
            {"context_id": f"legacy-{index}", "evidence_ids": [evidence_id]}
            for index, evidence_id in enumerate(retrieved, start=1)
        ]
    if not isinstance(raw_contexts, list):
        raise EvaluationInputError("retrieved_contexts must be a list")
    contexts: list[dict[str, Any]] = []
    seen_contexts: set[str] = set()
    for item in raw_contexts:
        if not isinstance(item, dict):
            raise EvaluationInputError("each retrieved context must be an object")
        context_id = item.get("context_id")
        if not isinstance(context_id, str) or not context_id:
            raise EvaluationInputError("retrieved context requires context_id")
        if context_id in seen_contexts:
            continue
        evidence_ids = _stable_ids(item.get("evidence_ids", []), field="evidence_id")
        contexts.append({"context_id": context_id, "evidence_ids": evidence_ids})
        seen_contexts.add(context_id)
    return contexts


def _safe_divide(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _dcg(relevances: Sequence[int]) -> float:
    return sum(
        relevance / math.log2(rank + 2) for rank, relevance in enumerate(relevances)
    )


def _percentile(values: Sequence[float], percentile: float) -> float:
    """Linear percentile, matching the common inclusive NumPy definition."""

    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _index_unique(
    rows: Iterable[dict[str, Any]], key: str, label: str
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        identifier = row.get(key)
        if not isinstance(identifier, str) or not identifier:
            raise EvaluationInputError(f"{label} row is missing non-empty {key}")
        if identifier in indexed:
            raise EvaluationInputError(f"duplicate {label} {key}: {identifier}")
        indexed[identifier] = row
    return indexed


def evaluate_records(
    cases: Sequence[dict[str, Any]],
    predictions: Sequence[dict[str, Any]],
    canonical_facts: Sequence[dict[str, Any]],
    *,
    strict_case_set: bool = True,
) -> dict[str, Any]:
    """Evaluate prediction records against frozen cases and canonical facts."""

    case_by_id = _index_unique(cases, "case_id", "case")
    prediction_by_id = _index_unique(predictions, "case_id", "prediction")
    fact_by_evidence = _index_unique(canonical_facts, "evidence_id", "canonical fact")
    case_ids = set(case_by_id)
    prediction_ids = set(prediction_by_id)
    missing = sorted(case_ids - prediction_ids)
    extra = sorted(prediction_ids - case_ids)
    if strict_case_set and (missing or extra):
        raise EvaluationInputError(
            f"prediction case set mismatch: missing={missing}, extra={extra}"
        )

    hit_sum = 0.0
    recall_numerator = 0
    recall_denominator = 0
    reciprocal_rank_sum = 0.0
    ndcg_sum = 0.0
    context_precision_sum = 0.0
    answerable_count = 0
    valid_citation_count = 0
    correct_citation_count = 0
    predicted_citation_count = 0
    required_citation_count = 0
    refusal_correct = 0
    answerable_false_refusals = 0
    unanswerable_false_answers = 0
    unanswerable_count = 0
    server_citation_total = 0
    server_citation_valid = 0
    evaluated_count = 0
    latencies: list[float] = []
    per_case: list[dict[str, Any]] = []

    for case in cases:
        case_id = case["case_id"]
        prediction = prediction_by_id.get(
            case_id,
            {
                "case_id": case_id,
                "retrieved": [],
                "citation_ids": [],
                "refused": False,
                "latency_ms": 0.0,
            },
        )
        try:
            retrieved = _stable_ids(prediction.get("retrieved", []))
            citations = _stable_ids(prediction.get("citation_ids", []))
            contexts = _retrieved_contexts(prediction, retrieved)
            offered_citations = _stable_ids(
                prediction.get("offered_citation_ids", []), field="citation_id"
            )
            generated_citations = _stable_ids(
                prediction.get("generated_citation_ids", []), field="citation_id"
            )
        except EvaluationInputError as exc:
            raise EvaluationInputError(f"prediction {case_id}: {exc}") from exc
        refused = prediction.get("refused")
        if not isinstance(refused, bool):
            raise EvaluationInputError(f"prediction {case_id}: refused must be boolean")
        latency = prediction.get("latency_ms")
        if (
            not isinstance(latency, (int, float))
            or isinstance(latency, bool)
            or not math.isfinite(latency)
            or latency < 0
        ):
            raise EvaluationInputError(
                f"prediction {case_id}: latency_ms must be a finite non-negative number"
            )
        latencies.append(float(latency))

        gold_evidence = _stable_ids(case.get("gold", {}).get("required_evidence", []))
        gold_set = set(gold_evidence)
        answerable = case.get("answerable") is True
        top5_contexts = contexts[:5]
        top10_contexts = contexts[:10]
        top5 = [
            evidence_id
            for context in top5_contexts
            for evidence_id in context["evidence_ids"]
        ]
        seen_relevant_evidence: set[str] = set()
        relevant_top5: list[bool] = []
        for context in top5_contexts:
            newly_relevant = (
                gold_set.intersection(context["evidence_ids"]) - seen_relevant_evidence
            )
            relevant_top5.append(bool(newly_relevant))
            seen_relevant_evidence.update(newly_relevant)

        case_hit = None
        case_recall = None
        case_mrr = None
        case_ndcg = None
        case_context_precision = None
        if answerable:
            if not gold_set:
                raise EvaluationInputError(
                    f"answerable case {case_id} has no required evidence"
                )
            answerable_count += 1
            matched_top5 = len(gold_set.intersection(top5))
            case_hit = 1.0 if matched_top5 else 0.0
            case_recall = matched_top5 / len(gold_set)
            case_context_precision = _safe_divide(
                sum(relevant_top5), len(top5_contexts)
            )
            first_relevant_rank = next(
                (
                    rank
                    for rank, context in enumerate(top10_contexts, start=1)
                    if gold_set.intersection(context["evidence_ids"])
                ),
                None,
            )
            case_mrr = 1.0 / first_relevant_rank if first_relevant_rank else 0.0
            ideal_relevances = [1] * min(len(gold_set), 5)
            case_ndcg = _safe_divide(
                _dcg([int(value) for value in relevant_top5]),
                _dcg(ideal_relevances),
            )
            hit_sum += case_hit
            recall_numerator += matched_top5
            recall_denominator += len(gold_set)
            reciprocal_rank_sum += case_mrr
            ndcg_sum += case_ndcg
            context_precision_sum += case_context_precision

        citation_set = set(citations)
        valid_citations = citation_set.intersection(fact_by_evidence)
        correct_citations = citation_set.intersection(gold_set)
        predicted_citation_count += len(citation_set)
        valid_citation_count += len(valid_citations)
        correct_citation_count += len(correct_citations)
        required_citation_count += len(gold_set)

        expected_refusal = not answerable
        case_refusal_correct = refused == expected_refusal
        refusal_correct += int(case_refusal_correct)
        if answerable:
            answerable_false_refusals += int(refused)
        else:
            unanswerable_count += 1
            unanswerable_false_answers += int(not refused)
        offered_set = set(offered_citations)
        server_citation_total += len(generated_citations)
        server_citation_valid += sum(
            citation_id in offered_set for citation_id in generated_citations
        )
        evaluated_count += 1
        per_case.append(
            {
                "case_id": case_id,
                "answerable": answerable,
                "hit_at_5": case_hit,
                "evidence_recall_at_5": case_recall,
                "mrr_at_10": case_mrr,
                "ndcg_at_5": case_ndcg,
                "context_precision_at_5": case_context_precision,
                "citation_ids_valid": len(valid_citations) == len(citation_set),
                "citation_precision": _safe_divide(
                    len(correct_citations), len(citation_set)
                ),
                "citation_recall": _safe_divide(len(correct_citations), len(gold_set)),
                "refusal_correct": case_refusal_correct,
                "false_refusal": answerable and refused,
                "false_answer": (not answerable) and (not refused),
                "server_citation_ids_valid": (
                    bool(generated_citations)
                    and all(
                        citation_id in offered_set
                        for citation_id in generated_citations
                    )
                ),
                "latency_ms": float(latency),
            }
        )

    metrics = {
        "hit_at_5": _safe_divide(hit_sum, answerable_count),
        "evidence_recall_at_5": _safe_divide(recall_numerator, recall_denominator),
        "mrr_at_10": _safe_divide(reciprocal_rank_sum, answerable_count),
        "ndcg_at_5": _safe_divide(ndcg_sum, answerable_count),
        "context_precision_at_5": _safe_divide(context_precision_sum, answerable_count),
        "citation_id_validity": _safe_divide(
            valid_citation_count, predicted_citation_count
        ),
        "citation_precision": _safe_divide(
            correct_citation_count, predicted_citation_count
        ),
        "citation_recall": _safe_divide(
            correct_citation_count, required_citation_count
        ),
        "refusal_accuracy": _safe_divide(refusal_correct, evaluated_count),
        "false_answer_rate_unanswerable": _safe_divide(
            unanswerable_false_answers, unanswerable_count
        ),
        "false_refusal_rate_answerable": _safe_divide(
            answerable_false_refusals, answerable_count
        ),
        "server_citation_id_validity": _safe_divide(
            server_citation_valid, server_citation_total
        ),
        "latency_p50_ms": _percentile(latencies, 0.50),
        "latency_p95_ms": _percentile(latencies, 0.95),
    }
    return {
        "dataset_label": "VERIFIED_SYNTHETIC",
        "counts": {
            "cases": evaluated_count,
            "answerable_cases": answerable_count,
            "required_evidence": recall_denominator,
            "predicted_citations": predicted_citation_count,
        },
        "metrics": metrics,
        "per_case": per_case,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    repo_root = Path(__file__).resolve().parents[1]
    parser.add_argument(
        "--predictions", type=Path, required=True, help="Prediction JSONL"
    )
    parser.add_argument(
        "--cases", type=Path, default=repo_root / "evaluation" / "frozen_cases.jsonl"
    )
    parser.add_argument(
        "--facts",
        type=Path,
        default=repo_root / "datasets" / "generated" / "canonical_facts.jsonl",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    args = parser.parse_args()
    result = evaluate_records(
        load_jsonl(args.cases),
        load_jsonl(args.predictions),
        load_jsonl(args.facts),
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8", newline="\n")
    print(rendered, end="")


if __name__ == "__main__":
    main()
