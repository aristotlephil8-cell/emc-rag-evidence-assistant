# Evaluation artifacts

`latest.json` and per-variant prediction JSONL files are generated only by the
frozen evaluation runner. The committed `latest.json` is a passed
`VERIFIED_SYNTHETIC` DashScope run against the committed synthetic corpus; it
records the exact corpus/configuration hashes, all gates, and limitations.

The report does not establish real-standard coverage, private-corpus quality,
laboratory accreditation, user-trial evidence, or production performance.

The default offline-provider Docker demo must not create or publish effectiveness
metrics in this directory.
