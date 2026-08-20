# Evaluation artifacts

`latest.json` and per-variant prediction JSONL files are generated only by the
frozen evaluation runner. They are intentionally absent until a real DashScope
run completes against the committed synthetic corpus and passes or truthfully
records all gates.

The default fake-provider Docker demo must not create or publish effectiveness
metrics in this directory.
