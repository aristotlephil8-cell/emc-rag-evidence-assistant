# Public synthetic EMC corpus

This directory contains a **VERIFIED_SYNTHETIC** teaching corpus. It is not a
copy of an EMC standard, a certification record, customer data, or evidence of
historical/production performance. Every standard number, device label, setup,
threshold, and component value is fictional and exists only to make retrieval
behavior reproducible.

## Frozen composition

- 16 logical documents: 12 primary and 4 distractors.
- 6 PDFs: 2 image-only scans, 2 vector tables, and 2 text PDFs.
- 5 DOCX and 5 UTF-8 TXT files.
- 40 canonical synthetic facts. A canonical fact and its exact locator are the
  only gold truth; distractor statements are never gold.

Each locator binds `document_id`, `section_id`, `page_number`, and `region`.
PDF regions use normalized page bounding boxes, DOCX regions add a paragraph
index, and TXT regions use a line span. These locators are fixture identities,
not claims that pagination is identical in every third-party renderer.

## Regeneration

From the repository root:

```bash
python datasets/scripts/generate_synthetic_corpus.py
python evaluation/validate_dataset.py
```

The generator uses only the Python standard library. DOCX ZIP timestamps, PDF
objects, manifest ordering, and generated timestamps are fixed. It writes the
binary fixtures under `datasets/generated/documents/`, canonical facts,
evaluation cases, and SHA-256 freeze proof. Regeneration is an explicit corpus
version operation; do not silently edit a frozen artifact.
