# Third-Party Notices

EMC_RAG is distributed under the repository-level Apache License 2.0. This notice records the upstream provenance of the DeepDOC-based scanned/complex-PDF path and its external model assets.

## InfiniFlow DeepDOC and RAGFlow

EMC_RAG adapts OCR detection, OCR recognition, layout detection, and table-structure preprocessing concepts/components from:

- [InfiniFlow DeepDOC model repository](https://huggingface.co/InfiniFlow/deepdoc)
- [InfiniFlow RAGFlow](https://github.com/infiniflow/ragflow)
- [RAGFlow Apache-2.0 license](https://github.com/infiniflow/ragflow/blob/main/LICENSE)

Upstream license: Apache License 2.0.

Pinned DeepDOC model revision:

```text
de0e793dc6d744406c96dabd688ccc969f41b443
```

External assets:

| File | Role |
| --- | --- |
| `det.onnx` | OCR detection |
| `rec.onnx` | OCR recognition |
| `ocr.res` | OCR character set |
| `layout.onnx` | Layout detection |
| `tsr.onnx` | Table-structure detection |

Exact source URLs, byte sizes, SHA-256 values, and expected ONNX input/output roles are defined in `backend/app/ingestion/deepdoc_assets_manifest.json`. The operator downloads these assets; model binaries are intentionally excluded from Git.

EMC_RAG modifications include:

- a strict five-file asset manifest;
- byte-size, SHA-256, and ONNX role validation before use;
- local request-time model loading;
- a 120-second worker deadline;
- a 2 GiB POSIX address-space cap plus a container memory limit;
- stable EMC_RAG page, bounding-box, table-row, and chunk locators;
- sanitized ingestion error codes;
- text-first PDF parsing with whole-document DeepDOC fallback for scans, tables, and complex layouts.

The adapted implementation retains upstream attribution in `backend/app/ingestion/THIRD_PARTY_NOTICES.md` and the relevant source header. The full Apache License 2.0 text is in `LICENSE`.
