# Evaluation Protocol / 评测协议

## Evidence label / 证据标签

All measurements generated from the committed fixtures must be labeled **`VERIFIED_SYNTHETIC`**. This label means only that the metric was produced reproducibly from the frozen public synthetic corpus. It does not mean real-standard coverage, laboratory accreditation, user-trial evidence, production performance, or historical responsibility.

所有由仓库内 fixtures 生成的指标必须标记为 **`VERIFIED_SYNTHETIC`**。该标签只表示结果来自冻结的公开合成语料，不表示真实标准覆盖、实验室认可、用户试用、生产效果或历史职责。

**Current snapshot / 当前快照：`VERIFIED_SYNTHETIC / PASSED`.** The committed [`artifacts/evaluation/latest.json`](../artifacts/evaluation/latest.json) records one DashScope run against the frozen public corpus, including configuration/corpus hashes, metrics, gates, and limitations. It is not evidence for real standards, private material, historical trials, or production performance.

## Frozen corpus / 冻结语料

| Contract | Value |
| --- | ---: |
| Logical documents | 16 |
| Primary / distractor | 12 / 4 |
| PDF | 6: 2 scan, 2 table, 2 text |
| DOCX / TXT | 5 / 5 |
| Questions | 40 |
| Answerable / unanswerable | 32 / 8 |
| Development / frozen test | 24 / 16 |
| Required evidence identities | 40 |

The 40 cases are grouped into 10 scenarios. A scenario group belongs to one split only, preventing near-duplicate leakage across dev and test. Eight answerable cases require two evidence items; the remaining 24 require one. Gold is the stable evidence identity plus exact document/locator binding, never keyword similarity.

The freeze proof is `evaluation/freeze_manifest.json`. It records SHA-256 values for the generator, corpus manifest, canonical facts, all 16 documents, and the frozen cases. Regenerating or editing a fixture is a corpus-version operation, not an invisible test adjustment.

## Compared variants / 对比方案

1. `bm25`: Elasticsearch lexical retrieval only.
2. `vector`: 1024-dimensional vector retrieval only.
3. `hybrid_rrf`: BM25 Top 50 + kNN Top 50, equal-weight RRF.
4. `hybrid_rrf_rerank`: equal-weight hybrid RRF plus reranking of Top 30.
5. `routed_hybrid_rrf_rerank`: exact-like query `0.7/0.3`, semantic query `0.3/0.7`, then Top-30 reranking and final Top 5.

RRF uses `k=60`. The final comparison variant is `routed_hybrid_rrf_rerank`; the baseline for effect gates is `bm25`.

## Metric definitions / 指标定义

Retrieval metrics are computed on answerable cases unless stated otherwise. Evidence and citation measurements use stable evidence IDs.

| Metric | Definition |
| --- | --- |
| Parser success rate | Successfully ingested logical documents divided by all 16 documents. |
| Hit@5 | Fraction of answerable cases with at least one required evidence item in Top 5. |
| Evidence Recall@5 | Micro recall of all required evidence identities in Top 5. |
| MRR@10 | Mean reciprocal rank of the first required evidence item within Top 10. |
| nDCG@5 | Binary evidence relevance, macro-averaged across answerable cases. |
| Context Precision@5 | Relevant required-evidence contexts divided by returned Top-5 contexts, macro-averaged across answerable cases. |
| Citation ID validity | Canonical evidence IDs divided by all predicted canonical citation IDs. |
| Server citation ID validity | Generated `S*` IDs that exist in the offered source-ID set divided by all generated `S*` IDs. |
| Citation precision | Correct required-evidence citations divided by predicted citations, micro-averaged. |
| Citation recall | Correct required-evidence citations divided by required citations, micro-averaged. |
| Refusal accuracy | Whether `refused` equals the case's unanswerable label, across all cases. |
| False answer rate | Unanswerable cases that were not refused divided by all unanswerable cases. |
| False refusal rate | Answerable cases that were refused divided by all answerable cases. |
| P50/P95 latency | Linear percentiles over non-negative measured request latencies. |

When no citations are produced, citation ratios are reported as zero, not as vacuous perfect scores. Automated exact-ID checks are not called human Claim Support.

## Threshold discipline / 阈值纪律

For each variant, the refusal threshold is selected on the 24-case dev split from a fixed grid. After selection, the configuration is locked and evaluated on the 16-case frozen test split. Test failures remain visible as Badcases; the frozen test must not be reused for iterative tuning.

The report also stores `runtime_config` and its SHA-256 `config_hash`. DashScope Demo startup recomputes this configuration from the active model, parser bundle, chunker, index and retrieval settings, then uses the locked final-variant threshold. A missing report, failed gates, or any configuration mismatch is fail-closed.

## Effect gates / 效果门禁

The final variant passes the positive-effect gate only when all conditions hold on frozen test:

- Evidence Recall@5 is not below BM25.
- nDCG@5 is strictly above BM25.
- Unanswerable false-answer rate is not worse than BM25.
- Server citation ID validity is exactly 100%.

If any gate fails, the report status is `failed`; no “improved by X%” resume statement should be generated. A fake-provider run is `contract_only` and cannot pass effectiveness gates.

## Badcase taxonomy / Badcase 分类

- `Parser`: a document could not be parsed or its frozen locator could not be resolved exactly.
- `RetrievalPool`: required evidence did not enter the final Top 5.
- `Rerank`: evidence present before reranking was removed from the final Top 5.
- `Citation`: generation failed or returned an offered/canonical citation mismatch.

The runner aborts on ambiguous or unmapped primary evidence instead of silently matching by query similarity.

## Reproduction / 复现

Validate only the frozen data contract:

```powershell
.\scripts\evaluate.ps1 -ValidateOnly
```

Run the online synthetic evaluation only after:

1. Elasticsearch is available at the configured URL.
2. The pinned DeepDOC assets can be downloaded and validated.
3. `DASHSCOPE_API_KEY` is present in the current process.
4. The operator accepts the outbound-data boundary.

```powershell
.\scripts\evaluate.ps1
```

The public artifact path is `artifacts/evaluation/latest.json`; per-variant predictions are written below `artifacts/evaluation/predictions/`. When the report is missing or invalid, `/api/v1/evaluation/latest` returns `status=not_run` and `evidence_status=NOT_VERIFIED` rather than presenting an effectiveness claim.
