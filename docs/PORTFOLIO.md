# EMC_RAG Reviewer Guide

This page is the shortest path from a GitHub link to an interview-ready technical discussion. EMC_RAG is the publicly shareable engineering and validation portion of the resume project; it contains no private EMC material and is not a complete archive of the historical system.

## Read the repository in 90 seconds

| Time | Read | What it establishes |
| --- | --- | --- |
| 0–30 s | [README](../README.md) screenshot and public evaluation card | Product shape, problem boundary, and a real runnable UI |
| 30–60 s | [`artifacts/evaluation/latest.json`](../artifacts/evaluation/latest.json) | One fixed public-synthetic run, five ablations, gates, and limitations |
| 60–90 s | [Architecture](ARCHITECTURE.md) and the code map below | The design can be traced from UI/API to source locators and validation |

## The engineering story

The repository follows one evidence-preserving path:

```text
PDF / DOCX / TXT
  -> text-first parsing or DeepDOC fallback
  -> structure-aware chunks with page / bbox / table locators
  -> SQLite lifecycle metadata + rebuildable Elasticsearch index
  -> BM25 + vector candidates + routed weighted RRF
  -> Qwen3 Rerank
  -> structured claims + allowed citation IDs
  -> server validation, refusal, or needs_review
```

This is deliberately narrower than a production platform: no authentication, multi-tenancy, queue, PostgreSQL, Redis, or private corpus is claimed. The narrow scope makes the ingestion, retrieval, citation, and evaluation decisions inspectable.

## Candidate contribution and ownership boundary

The candidate served as core developer for the EMC_RAG project. This repository makes four parts inspectable:

| Contribution | Evidence in this repository | Boundary |
| --- | --- | --- |
| OCR, structure-aware parsing, metadata, idempotent writes, and index versions | [`backend/app/ingestion/`](../backend/app/ingestion/), [`backend/app/services.py`](../backend/app/services.py) | Public synthetic fixtures only |
| BM25/vector retrieval, RRF, query routing, and reranking | [`backend/app/search.py`](../backend/app/search.py) | Public-synthetic effectiveness is separate from historical results |
| Citation binding, insufficient-evidence refusal, and review state | [`backend/app/providers.py`](../backend/app/providers.py), [`backend/app/services.py`](../backend/app/services.py) | Citation identity validation is not semantic truth proof |
| Layered evaluation and regression tests | [`evaluation/`](../evaluation/), [`backend/tests/`](../backend/tests/) | Historical corpus and trial logs are not published |

DeepDOC/RAGFlow-derived components and model assets are attributed in [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md). The repository does not claim ownership of those upstream assets.

## Code map for interview questions

| Question an interviewer may ask | Starting point |
| --- | --- |
| How do files become citeable evidence? | [`backend/app/services.py`](../backend/app/services.py), [`backend/app/chunking.py`](../backend/app/chunking.py), [`backend/app/ingestion/`](../backend/app/ingestion/) |
| How are BM25, kNN, RRF, routing, and reranking connected? | [`backend/app/search.py`](../backend/app/search.py) |
| How do you prevent fabricated citations? | [`backend/app/providers.py`](../backend/app/providers.py), `ChatService` in [`backend/app/services.py`](../backend/app/services.py) |
| How are dev tuning and frozen-test leakage prevented? | [`backend/scripts/run_evaluation.py`](../backend/scripts/run_evaluation.py), [`evaluation/`](../evaluation/) |
| How does the UI expose evidence and degradation? | [`frontend/src/`](../frontend/src/) |

## Evidence ledger

### Public repository evidence

The committed report is `VERIFIED_SYNTHETIC / PASSED` on 16 logical synthetic documents and 40 frozen questions. It records a 100% parser success rate and, for the routed final variant, Evidence Recall@5 / MRR@10 / nDCG@5 of 100% / 1.000 / 1.000. It also records Citation Precision 94.4%, Citation Recall 100%, server citation-ID validity 100%, refusal accuracy 93.8%, P95 retrieval latency 261 ms, and P95 generation latency 2.65 s.

Those numbers are reproducible only under the committed public-synthetic protocol and the report's fixed model/configuration hash. They are not claims about real EMC standards, private material, accredited laboratory use, users, or production traffic.

### EMC_RAG project historical results

The EMC_RAG project from May 2023 to May 2024 reports 160 documents (parser acceptance 82.5% to 95.6%), 120 questions / 164 required evidence identities (Evidence Recall@5 68.3% to 84.1%, MRR 0.56 to 0.74), and 80 fixed questions with atomized citation review (citation accuracy 92.4%, unsupported-claim rate 16.8% to 5.6%). It also reports a 12-person, four-week, 3,200-request trial.

These are **historical project measurements**, not outputs reproduced by this repository. The historical corpus, logs, and raw annotations are not published here. Do not compare them numerically with the current public-synthetic report or cite this repository as their source of proof.

## Badcases and trade-offs

See [Badcases](BADCASES.md) for the condition, symptom, root cause, change, verification, and remaining boundary of the public locator-regression case. In normal operation, a low top-evidence score returns `insufficient_evidence`; retrieval degradation or invalid citations return `needs_review` rather than silently lowering the evidence bar.

## Run it locally

```powershell
.\scripts\start.ps1 -Provider fake
```

Open `http://localhost:5173`, upload a committed synthetic fixture, and inspect the retrieval trace and source locator cards. Use DashScope only for content you are authorized to send to its APIs; see the [security boundary](SECURITY.md).

## Good interview follow-ups

1. Why use RRF instead of score normalization across BM25 and kNN?
2. What does a page/bbox/table locator prove, and what does it not prove?
3. How would you replace synthetic Gold with an independently reviewed real EMC corpus?
4. Which current components would need to change before multi-tenant production use?
