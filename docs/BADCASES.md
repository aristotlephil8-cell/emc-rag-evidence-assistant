# EMC_RAG Badcases / 失败路径与回归

本页只记录公开合成语料上可复现的工程回归；它不替代真实 EMC 试验的根因分析，也不公开历史项目的内部文档或日志。

## B-001：PDF 定位器过宽导致证据身份歧义

| 项目 | 记录 |
| --- | --- |
| 条件 | 冻结评测按 `document + page + bbox + table_index + table_row` 识别必需证据；普通 PDF 文本和表格行都必须映射到唯一定位器。 |
| 现象 | 过宽的 PDF 文本框或整表框会覆盖多个候选区域，使评测无法把检索命中唯一绑定到 Gold 证据。系统按设计将这种情况视为歧义，而不是根据关键词猜测正确证据。 |
| 根因 | 普通 PDF 的相邻文本行尚未合并为紧凑文本块；表格行需要独立的行级 bbox 与 `table_index + table_row`，仅有页码或整表范围不足以唯一定位。 |
| 修改 | 文本路径合并相邻行并输出 `tight_text_block`；DeepDOC 表格路径为每行输出紧凑 bbox、表序号和行号。 |
| 验证 | [`test_parse_plain_pdf_with_page_locator`](../backend/tests/test_ingestion.py) 断言普通 PDF 不使用整页 bbox；[`test_table_locator_requires_index_and_compact_row_bbox`](../backend/tests/test_evaluation_runner.py) 断言表格定位要求精确行级条件；[`test_all_40_frozen_evidence_ids_have_one_exact_runtime_locator`](../backend/tests/test_evaluation_runner.py) 审计 40 条冻结证据的一一映射。 |
| 剩余边界 | 该回归只覆盖公开合成 fixtures；复杂真实版面、低清扫描件和跨页表格仍需要人工核验来源定位。 |

## 失败时的安全行为

- 证据分数不足：返回 `insufficient_evidence`，不生成事实性结论。
- Rerank、检索或结构化引用校验异常：返回 `needs_review`，不静默降低证据质量。
- 引用 ID 不在服务端提供的 allow-list：拒绝输出该答案。

这些行为证明的是流程与引用身份校验，不证明引用文本一定在语义上支持主张；涉及 EMC 合规或整改的最终判断仍由工程师复核。
