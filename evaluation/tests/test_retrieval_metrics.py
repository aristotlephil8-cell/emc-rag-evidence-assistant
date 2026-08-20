from __future__ import annotations

import math
import unittest

from evaluation.retrieval_metrics import EvaluationInputError, evaluate_records


class RetrievalMetricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cases = [
            {
                "case_id": "a",
                "answerable": True,
                "gold": {
                    "required_evidence": [{"evidence_id": "e1"}, {"evidence_id": "e2"}]
                },
            },
            {
                "case_id": "b",
                "answerable": True,
                "gold": {"required_evidence": [{"evidence_id": "e3"}]},
            },
            {
                "case_id": "c",
                "answerable": False,
                "gold": {"required_evidence": []},
            },
        ]
        self.facts = [
            {"evidence_id": "e1"},
            {"evidence_id": "e2"},
            {"evidence_id": "e3"},
        ]

    def test_all_metric_definitions(self) -> None:
        predictions = [
            {
                "case_id": "a",
                "retrieved": ["e1", "noise", "e2", "e1"],
                "citation_ids": ["e1", "invalid", "e1"],
                "refused": False,
                "latency_ms": 10,
            },
            {
                "case_id": "b",
                "retrieved": ["noise", "e3"],
                "citation_ids": ["e3"],
                "refused": False,
                "latency_ms": 20,
            },
            {
                "case_id": "c",
                "retrieved": [],
                "citation_ids": [],
                "refused": True,
                "latency_ms": 30,
            },
        ]
        result = evaluate_records(self.cases, predictions, self.facts)
        metrics = result["metrics"]
        expected_a_ndcg = (1 + 1 / math.log2(4)) / (1 + 1 / math.log2(3))
        expected_b_ndcg = 1 / math.log2(3)
        self.assertAlmostEqual(metrics["hit_at_5"], 1.0)
        self.assertAlmostEqual(metrics["evidence_recall_at_5"], 1.0)
        self.assertAlmostEqual(metrics["mrr_at_10"], 0.75)
        self.assertAlmostEqual(
            metrics["ndcg_at_5"], (expected_a_ndcg + expected_b_ndcg) / 2
        )
        self.assertAlmostEqual(
            metrics["context_precision_at_5"], ((2 / 3) + (1 / 2)) / 2
        )
        self.assertAlmostEqual(metrics["citation_id_validity"], 2 / 3)
        self.assertAlmostEqual(metrics["citation_precision"], 2 / 3)
        self.assertAlmostEqual(metrics["citation_recall"], 2 / 3)
        self.assertAlmostEqual(metrics["refusal_accuracy"], 1.0)
        self.assertAlmostEqual(metrics["false_answer_rate_unanswerable"], 0.0)
        self.assertAlmostEqual(metrics["false_refusal_rate_answerable"], 0.0)
        self.assertAlmostEqual(metrics["server_citation_id_validity"], 0.0)
        self.assertAlmostEqual(metrics["latency_p50_ms"], 20.0)
        self.assertAlmostEqual(metrics["latency_p95_ms"], 29.0)

    def test_no_citations_is_not_vacuously_perfect(self) -> None:
        predictions = [
            {
                "case_id": "a",
                "retrieved": [],
                "citation_ids": [],
                "refused": False,
                "latency_ms": 1,
            },
            {
                "case_id": "b",
                "retrieved": [],
                "citation_ids": [],
                "refused": False,
                "latency_ms": 1,
            },
            {
                "case_id": "c",
                "retrieved": [],
                "citation_ids": [],
                "refused": True,
                "latency_ms": 1,
            },
        ]
        metrics = evaluate_records(self.cases, predictions, self.facts)["metrics"]
        self.assertEqual(metrics["citation_id_validity"], 0.0)
        self.assertEqual(metrics["citation_precision"], 0.0)
        self.assertEqual(metrics["citation_recall"], 0.0)

    def test_context_precision_counts_contexts_not_flattened_evidence(self) -> None:
        predictions = [
            {
                "case_id": "a",
                "retrieved": ["e1", "e2", "noise"],
                "retrieved_contexts": [
                    {"context_id": "chunk-1", "evidence_ids": ["e1", "e2"]},
                    {"context_id": "chunk-2", "evidence_ids": ["noise"]},
                ],
                "citation_ids": [],
                "offered_citation_ids": ["S1", "S2"],
                "generated_citation_ids": ["S1"],
                "refused": False,
                "latency_ms": 1,
            },
            {
                "case_id": "b",
                "retrieved": ["e3"],
                "retrieved_contexts": [
                    {"context_id": "chunk-3", "evidence_ids": ["e3"]}
                ],
                "citation_ids": [],
                "offered_citation_ids": ["S1"],
                "generated_citation_ids": ["S99"],
                "refused": False,
                "latency_ms": 1,
            },
            {
                "case_id": "c",
                "retrieved": [],
                "retrieved_contexts": [],
                "citation_ids": [],
                "offered_citation_ids": [],
                "generated_citation_ids": [],
                "refused": True,
                "latency_ms": 1,
            },
        ]

        metrics = evaluate_records(self.cases, predictions, self.facts)["metrics"]

        self.assertAlmostEqual(metrics["context_precision_at_5"], 0.75)
        self.assertAlmostEqual(metrics["server_citation_id_validity"], 0.5)

    def test_duplicate_context_for_same_evidence_has_no_second_ndcg_gain(self) -> None:
        predictions = [
            {
                "case_id": "a",
                "retrieved": ["e1"],
                "retrieved_contexts": [
                    {"context_id": "chunk-1", "evidence_ids": ["e1"]},
                    {"context_id": "chunk-2", "evidence_ids": ["e1"]},
                ],
                "citation_ids": [],
                "refused": False,
                "latency_ms": 1,
            },
            {
                "case_id": "b",
                "retrieved": ["e3"],
                "retrieved_contexts": [
                    {"context_id": "chunk-3", "evidence_ids": ["e3"]}
                ],
                "citation_ids": [],
                "refused": False,
                "latency_ms": 1,
            },
            {
                "case_id": "c",
                "retrieved": [],
                "citation_ids": [],
                "refused": True,
                "latency_ms": 1,
            },
        ]

        result = evaluate_records(self.cases, predictions, self.facts)
        case_a = next(item for item in result["per_case"] if item["case_id"] == "a")

        self.assertLessEqual(case_a["ndcg_at_5"], 1.0)
        self.assertAlmostEqual(case_a["context_precision_at_5"], 0.5)

    def test_prediction_case_set_is_strict(self) -> None:
        with self.assertRaises(EvaluationInputError):
            evaluate_records(
                self.cases,
                [
                    {
                        "case_id": "a",
                        "retrieved": [],
                        "citation_ids": [],
                        "refused": False,
                        "latency_ms": 1,
                    }
                ],
                self.facts,
            )

    def test_invalid_latency_is_rejected(self) -> None:
        predictions = [
            {
                "case_id": case["case_id"],
                "retrieved": [],
                "citation_ids": [],
                "refused": False,
                "latency_ms": 1,
            }
            for case in self.cases
        ]
        predictions[0]["latency_ms"] = float("nan")
        with self.assertRaises(EvaluationInputError):
            evaluate_records(self.cases, predictions, self.facts)


if __name__ == "__main__":
    unittest.main()
