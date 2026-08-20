# Third-party notice: InfiniFlow DeepDOC

EMC_RAG's scanned/complex PDF path adapts OCR detection, OCR recognition,
layout detection, and table-structure preprocessing from InfiniFlow DeepDOC
and RAGFlow. Those upstream components and the five model assets are licensed
under Apache License 2.0.

- Upstream: `InfiniFlow/deepdoc` and `infiniflow/ragflow`
- Pinned model revision: `de0e793dc6d744406c96dabd688ccc969f41b443`
- Assets: `det.onnx`, `rec.onnx`, `ocr.res`, `layout.onnx`, `tsr.onnx`
- License: Apache-2.0

EMC_RAG modifications include a strict five-file manifest, SHA-256 and ONNX
input/output role validation, local-only request-time model loading, a 120
second worker deadline, a 2 GiB POSIX address-space cap, stable EMC_RAG section
locators, and explicit safe error codes. Model binaries are downloaded by the
operator and are intentionally excluded from the repository.

The Apache License 2.0 text is provided by the repository-level `LICENSE`.
