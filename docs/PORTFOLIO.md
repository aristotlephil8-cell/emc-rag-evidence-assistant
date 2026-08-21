# EMC_RAG 项目真实性核对

这个页面不负责宣传产品功能，而是把项目经历拆成可以逐项核对的证据链：项目为什么做、本人负责什么、代码在哪里、遇到什么问题、比较过哪些方案、为什么选择当前方案，以及结果在什么条件下成立。

## 一、项目背景

EMC 试验与整改过程中，标准、试验规程、设备手册和历史整改案例通常格式复杂、版本分散。工程人员既需要精确查询标准编号和条款，也需要根据故障现象检索相似案例；扫描件、表格、条款层级和来源定位会直接影响检索质量与结论复核。

项目围绕传导发射（CE）与辐射发射（RE）场景，将专业资料转化为可检索、可引用、可评测的领域知识。系统输出候选分析、证据依据和复验建议，最终判断仍由 EMC 工程师完成。

## 二、项目演进主线

```text
业务问题
→ 需求收敛
→ 基础 RAG 原型
→ 复杂文档入库优化
→ 混合检索与 Rerank
→ 证据约束回答
→ 评测、Badcase 回归与效果验证
```

每个阶段都按照同一条链路展开：

```text
上一阶段的基线
→ 具体 Badcase
→ 根因定位
→ 候选方案比较
→ 最终选择与代价
→ 代码实现
→ 测试/评测验证
→ 剩余边界
```

## 三、个人贡献与代码入口

本人在 EMC_RAG 项目中担任核心开发。公开仓库中的个人贡献按以下方式核对：

| 个人贡献 | 代码与测试入口 | 需要能够解释的问题 |
|---|---|---|
| 文档解析入库：OCR、结构感知切分、元数据、幂等写入和索引版本 | [`backend/app/ingestion/`](../backend/app/ingestion/)、[`backend/app/chunking.py`](../backend/app/chunking.py)、[`backend/app/services.py`](../backend/app/services.py) | 为什么要保留 locator、版本和状态；失败后如何重试和恢复 |
| 混合检索与精排：BM25、向量检索、RRF、查询路由和 Reranker | [`backend/app/search.py`](../backend/app/search.py) | 为什么不能只用关键词或向量；RRF 和 Rerank 各自解决什么问题 |
| 证据约束回答：claims、citation IDs、证据不足拒答和 review 状态 | [`backend/app/providers.py`](../backend/app/providers.py)、[`backend/app/services.py`](../backend/app/services.py) | 引用身份校验能证明什么；什么时候必须拒答或转人工 |
| 分层评测与 Badcase 回归 | [`evaluation/`](../evaluation/)、[`backend/tests/`](../backend/tests/)、[`docs/BADCASES.md`](BADCASES.md) | 指标如何定义；如何区分解析、召回、排序和回答问题 |

## 四、核心架构与代码核对

```text
PDF / DOCX / TXT
  → 文本优先解析或 DeepDOC 补偿
  → 结构化 Chunk 与页码/表格/章节 locator
  → SQLite 生命周期记录与可重建 Elasticsearch 索引
  → BM25 + 向量候选召回
  → 查询路由与加权 RRF
  → Qwen3 Rerank
  → 结构化 claims + 允许的 citation IDs
  → 服务端校验、拒答或 needs_review
```

| 架构问题 | 代码入口 | 核对重点 |
|---|---|---|
| 文件如何变成可引用证据 | [`backend/app/ingestion/`](../backend/app/ingestion/)、[`backend/app/chunking.py`](../backend/app/chunking.py) | 结构和来源信息是否在解析阶段保留 |
| 业务事实与检索索引如何协作 | [`backend/app/services.py`](../backend/app/services.py) | 状态、稳定 ID、幂等和重建关系 |
| 多路召回如何融合并排序 | [`backend/app/search.py`](../backend/app/search.py) | 精确查询、语义查询、RRF 和 Rerank 的职责边界 |
| 生成结果如何回到证据 | [`backend/app/providers.py`](../backend/app/providers.py)、[`backend/app/services.py`](../backend/app/services.py) | citation ID 校验、证据不足和降级状态 |
| 前端如何展示检索过程 | [`frontend/src/components/`](../frontend/src/components/) | 文档状态、检索轨迹和来源定位是否一致 |

## 五、Badcase 与方案取舍

Badcase 不是附加说明，而是判断设计是否真实的重要入口。每个案例应保持以下结构：

```text
条件
→ 现象
→ 候选原因
→ 根因
→ 修复方案
→ 验证结果
→ 剩余边界
```

当前公开案例见 [Badcases](BADCASES.md)。重点核对：

1. 问题是否能定位到具体模块，而不是笼统归因于“大模型”；
2. 是否比较过至少一种替代方案；
3. 最终方案解决了什么，同时增加了什么成本；
4. 修复是否有测试、评测或回归结果支撑。

重点设计取舍包括：

| 问题 | 候选方案 | 当前选择 | 选择原因 |
|---|---|---|---|
| 复杂文档如何解析 | 统一文本提取 / 全量 OCR / 格式路由与按需补偿 | 格式路由与按需补偿 | 尽量保留原生结构，同时覆盖扫描件和表格 |
| 精确查询与语义查询如何兼顾 | 仅 BM25 / 仅向量 / 直接分数相加 / 混合召回后融合 | BM25 + 向量 + RRF | 同时覆盖编号匹配和自然语言现象，避免直接比较不同分数空间 |
| 如何提高候选排序 | 扩大 Top-K / 只靠融合 / 小候选集 Rerank | 小候选集 Rerank | 在排序质量、延迟和成本之间折中 |
| 如何控制无依据回答 | 只在 Prompt 中要求引用 / 结构化证据与服务端校验 | 结构化证据与服务端校验 | 将引用身份、证据不足和 review 状态变成可测试契约 |

## 六、评测结果与条件

公开仓库评测使用冻结的公开合成 EMC 资料和问题集。当前报告记录：

- 16 份逻辑文档、40 个固定问题、40 条 required evidence；
- Parser 成功率 100%；
- Routed Hybrid RRF + Rerank 的 Evidence Recall@5 / MRR@10 / nDCG@5 为 `100% / 1.000 / 1.000`；
- Citation Precision / Recall 为 `94.4% / 100%`；
- 服务端 Citation ID 有效性为 `100%`；
- 拒答准确率为 `93.8%`；
- P95 检索 / 生成延迟为 `261 ms / 2.65 s`。

完整指标定义、配置哈希、语料哈希和门禁规则见 [评测协议](EVALUATION.md) 与 [`latest.json`](../artifacts/evaluation/latest.json)。

这些结果用于验证当前公开实现和评测协议。简历中的历史项目结果单独记录，不能用当前合成评测替代；同样，当前合成评测也不反向证明历史项目的全部指标。

## 七、历史项目与公开仓库的关系

历史项目经历为 `2023.05—2024.05` 的 EMC_RAG 项目，公开仓库提供其中可公开的工程实现和独立公开评测部分。

历史项目中的真实资料、原始标注、试用日志和内部运行环境不随仓库发布。README 中出现的历史指标必须明确标注为历史项目结果，不能与当前 `VERIFIED_SYNTHETIC` 报告混写。

## 八、复现入口

```powershell
.\scripts\start.ps1 -Provider offline
```

启动后打开 `http://localhost:5173`，上传 `datasets/generated/documents/` 中的公开合成样例，查看文档状态、检索轨迹和来源定位。

offline Provider 只验证接口、入库、SSE 和 UI 流程；公开效果结果来自固定评测报告，不来自 offline Provider。

## 九、第三方与数据来源

DeepDOC/RAGFlow 相关组件、模型资产、数据和示例的来源及许可边界见 [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)。公开仓库不包含内部 EMC 资料、个人简历、凭据、付费标准全文或未授权数据。
