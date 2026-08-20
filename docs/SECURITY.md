# Security and Data Boundary / 安全与数据边界

EMC_RAG is a local portfolio demo. It does not implement authentication, authorization, multi-tenancy, tenant isolation, audit retention, or production-grade secret management. Do not expose it directly to an untrusted network or treat it as a production deployment template.

EMC_RAG 是本地作品集 Demo，不包含认证、授权、多租户、租户隔离、审计留存或生产级密钥管理。不要把它直接暴露到不可信网络，也不要当作生产部署模板。

## Outbound data / 数据外发

Provider mode materially changes the data boundary:

| Mode | Outbound model data |
| --- | --- |
| `fake` | No Embedding/Rerank/Generation model API call. Deterministic placeholders only. |
| `dashscope` | Document chunks go to Embedding; query and candidates go to Rerank; query and final Top 5 evidence go to Generation. |

Before using DashScope, confirm that you own or are authorized to process and transmit every uploaded document. Do not upload confidential, personal, export-controlled, customer, internal laboratory, or licensed-standard content without the corresponding permission and policy review.

启用 DashScope 前，必须确认你有权处理并向远端服务传输所有上传内容。未经相应授权和策略审查，不得上传机密、个人、出口管制、客户、内部实验室资料或受许可约束的标准全文。

## Secrets / 凭据

- Keep `DASHSCOPE_API_KEY` in the current process or an approved local secret manager.
- Never put a real key in `.env.example`, source code, command history, screenshots, logs, issues, PR text, or committed files.
- `.env` is ignored by Git, but an ignored plaintext file is still a local secret risk.
- CI uses the fake Provider and must not store or require a DashScope secret.
- If a credential is exposed, revoke/rotate it through the provider; deleting it from the latest file is not sufficient once history or logs contain it.

## Network / 网络

Docker Compose binds the frontend, backend, and Elasticsearch host ports to `127.0.0.1` by default. Elasticsearch security is disabled for this single-host demo and therefore must not be rebound to a public interface without a separate security design.

The backend CORS allowlist is configuration-driven. The default local origins do not replace authentication.

## File-processing controls / 文件处理控制

- Only PDF, DOCX, and UTF-8 TXT are accepted.
- Upload size is limited to 20 MiB; PDF page count is limited to 50.
- Paths use the basename of the uploaded filename; user-supplied filesystem paths are not used for storage.
- PDF parsing rejects signature mismatch, encryption, corruption, oversize input, and excessive pages with sanitized error codes.
- DeepDOC runs through a worker boundary with a 120-second deadline. Linux/POSIX uses a 2 GiB address-space limit; Docker also limits backend memory to 2 GiB.
- Parsed text, section, and chunk counts have explicit caps.
- Model files are verified against a pinned manifest containing exact size, SHA-256, and ONNX input/output roles.

These controls reduce risk but do not make arbitrary untrusted document processing safe enough for a public upload service. The application has no malware sandbox, content-disarm pipeline, rate limiter, quota, or abuse detection.

## Persistence and deletion / 持久化与删除

SQLite metadata, Elasticsearch indexes, and DeepDOC assets are stored in named Docker volumes. `scripts/stop.ps1` stops containers but deliberately preserves those volumes. The repository exposes no document-deletion API and makes no data-retention guarantee. Operators must manage local volumes according to their own policy.

## Error and citation behavior / 错误与引用行为

- Public provider errors are reduced to sanitized codes; credentials and raw provider bodies are not returned.
- Retrieval degradation is visible in the trace. Chat fails closed with `needs_review` when reranking or retrieval integrity fails.
- Every factual generated claim must carry at least one offered citation ID; invalid or missing citations are not streamed as an answer.
- Low-confidence evidence yields `insufficient_evidence` instead of a factual answer.

These mechanisms validate citation identity, not semantic truth. A structurally valid citation can still fail to support a claim; final use in an EMC decision requires human review of the cited source.

## Public-repository boundary / 公开仓库边界

The repository may contain only public synthetic fixtures, source code, tests, documentation, and sanitized reproducibility artifacts. It must not contain:

- real credentials or `.env`;
- DeepDOC model binaries;
- private evaluation data;
- temporary runtime folders;
- a personal resume or contact details;
- local reference-project paths or copied private configuration;
- customer/internal EMC documents or real standards without redistribution rights.

See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) for upstream attribution and model-asset provenance.
