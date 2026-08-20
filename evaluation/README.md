# Synthetic evaluation contract

All results produced with this data must be labeled **VERIFIED_SYNTHETIC**.
They describe only the committed public fixtures and must not be presented as
real-standard coverage, laboratory accreditation, user-trial evidence, or
production effectiveness.

## Frozen cases

`frozen_cases.jsonl` contains exactly 40 cases:

- 32 answerable and 8 unanswerable;
- 24 development and 16 frozen-test cases;
- exactly 40 required-evidence entries;
- 10 scenario groups of four. A group belongs to one split only.

Eight answerable cases require two evidence items; the other 24 require one.
Every canonical evidence item is used exactly once. Gold is exact stable
evidence identity plus `document_id / section_id / page_number / region`, never
keyword similarity. Questions explicitly say that they are synthetic.

Do not tune on the frozen test split. Select refusal thresholds on `dev`, lock
configuration, then execute `test` once and retain failures as bad cases.

## Validation

```bash
python evaluation/validate_dataset.py
python -m unittest discover -s evaluation/tests -v
```

Validation checks exact format/role/count contracts, binary file shape, stable
locator binding, one-time evidence use, scenario-group isolation, cross-split
near duplicates, generator hash, and every frozen artifact hash.

## Prediction and metric contract

`retrieval_metrics.py` accepts one JSONL row per frozen case:

```json
{"case_id":"syn-q001","retrieved":[{"evidence_id":"syn-ev-0001"}],"citation_ids":["syn-ev-0001"],"refused":false,"latency_ms":42.5}
```

Run:

```bash
python evaluation/retrieval_metrics.py --predictions path/to/predictions.jsonl
```

Definitions:

- Hit@5: fraction of answerable cases with any required evidence in Top 5.
- Evidence Recall@5: micro required-evidence recall in Top 5.
- MRR@10: mean reciprocal rank of the first required evidence.
- nDCG@5: binary evidence relevance, macro-averaged on answerable cases.
- Context Precision@5: required evidence divided by returned contexts in Top 5,
  macro-averaged on answerable cases.
- Citation ID validity: canonical citation IDs divided by all predicted IDs.
- Citation precision/recall: exact required-evidence identity, micro-averaged.
- Refusal accuracy: `refused == (not answerable)` over all cases.
- P50/P95 latency: linear percentiles over non-negative `latency_ms`.

The tool reports zero—not a vacuous perfect score—when no citations are made.
