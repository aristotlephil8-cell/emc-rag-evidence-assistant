"""Parser versions without importing optional OCR runtime dependencies."""

DEEPDOC_REVISION = "de0e793dc6d744406c96dabd688ccc969f41b443"
TXT_PARSER_VERSION = "txt-1.0.0"
DOCX_PARSER_VERSION = "docx-1.1.0"
PDF_PLAIN_PARSER_VERSION = "pdf-plain-1.2.0"
DEEPDOC_PARSER_VERSION = f"deepdoc-{DEEPDOC_REVISION[:12]}-1.2.0"

PARSER_BUNDLE_VERSION = (
    "cvrag-parser-bundle-2.1.0"
    f"+txt={TXT_PARSER_VERSION}"
    f".docx={DOCX_PARSER_VERSION}"
    f".pdf={PDF_PLAIN_PARSER_VERSION}"
    f".deepdoc={DEEPDOC_PARSER_VERSION}"
)

__all__ = [
    "DEEPDOC_PARSER_VERSION",
    "DEEPDOC_REVISION",
    "DOCX_PARSER_VERSION",
    "PARSER_BUNDLE_VERSION",
    "PDF_PLAIN_PARSER_VERSION",
    "TXT_PARSER_VERSION",
]
