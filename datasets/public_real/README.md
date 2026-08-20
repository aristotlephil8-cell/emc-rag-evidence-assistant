# 公开真实 EMC 参考语料

本目录存放经过来源与再分发边界筛选的真实公开资料，供本地或展示环境手动上传验证 PDF 解析、定位、检索与引用链路。

它**独立于** `datasets/generated/` 的冻结合成评测集：

- 状态为 `PUBLIC_REFERENCE_NOT_EVALUATED`，不产生或支撑 Recall、nDCG、引用准确率、延迟等效果指标。
- 不会被 `scripts/evaluate.ps1` 自动读取，也不会改变既有 Gold、split 或拒答阈值。
- 每份资料的原始链接、下载日期、SHA-256 和使用边界见 [SOURCES.md](SOURCES.md) 与 [manifest.json](manifest.json)。

## 内容与用途

| 文件 | 真实内容 | 适合演示的能力 |
| --- | --- | --- |
| `msfc-spec-521-rev-d-2025.pdf` | NASA 设备与子系统 EMC 要求，包含 CE/RE/CS/RS、接地、屏蔽、测试与验证条款 | 长 PDF、目录、表格、条款与页码引用 |
| `fcc-da-24-415-test-site-validation.pdf` | FCC 关于设备授权标准更新的公开说明，含辐射发射测试场地验证信息 | 短文档、频段与测试场地问答 |
| `uk-ds-0071-22-designated-standards-emc.pdf` | 英国 EMC 指定标准的公开目录通知 | 表格、标准号检索与精确查询路由 |
| `cn-emc-response-and-improvement-2015.pdf` | 中文论文《电磁兼容应对及其改进技术》，覆盖接地、滤波、隔离、屏蔽、ESD 与测试整改 | 中文技术问答、整改路径与来源引用 |

## 使用规则

1. 上传前先阅读每份文件在 `SOURCES.md` 中的适用范围；它们不是中国市场合规结论，也不能替代购买的 IEC/CISPR/GB 标准全文。
2. 不得把客户测试报告、未脱敏整改记录、付费标准全文或受限资料混入本目录；具有合法本地使用权的私有资料只可放在 Git 忽略的 `datasets/private_local/`。
3. 若启用 DashScope，文档切块和检索候选会发送到远端模型服务；仅处理有权外发的内容。
4. 新增资料必须先记录来源、明确再分发依据、计算 SHA-256；来源或权利不清的资料只保留 URL，不提交文件。

这些 PDF 均保持原样；EMC_RAG 的 Apache-2.0 许可证不改变原始文件的权利归属。
