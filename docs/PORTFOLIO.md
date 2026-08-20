# CVRAG Reviewer Guide

This page is the shortest path from a GitHub link to an interview-ready technical discussion. CVRAG is a public portfolio implementation of an evidence-first EMC RAG workflow; it is not an archive of a historical internal system and contains no private EMC material.

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

### Resume-reported historical context

The resume describes an emcRAG project from May 2023 to May 2024. Its stated measurements include 160 documents (parser acceptance 82.5% to 95.6%), 120 questions / 164 required evidence identities (Evidence Recall@5 68.3% to 84.1%, MRR 0.56 to 0.74), and 80 fixed questions with atomized citation review (citation accuracy 92.4%, unsupported-claim rate 16.8% to 5.6%). It also reports a 12-person, four-week, 3,200-request trial.

These are **resume-reported historical measurements**, not outputs reproduced by this repository. The historical corpus, logs, and raw annotations are not published here. Do not compare them numerically with the current public-synthetic report or cite this repository as their source of proof.

## Deliberate Badcase and trade-offs

- A low top-evidence score returns `insufficient_evidence`; the system does not synthesize a confident answer.
- Retrieval degradation returns `needs_review` rather than silently falling back to a different quality level.
- Citation IDs must be in the server-offered allow-list. A malformed or out-of-set ID fails validation.
- The public corpus is synthetic by design. Public real reference PDFs are demonstrative only and are not mixed into the frozen benchmark.

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
