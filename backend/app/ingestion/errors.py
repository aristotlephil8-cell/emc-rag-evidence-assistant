from __future__ import annotations

from enum import Enum


class IngestionErrorCode(str, Enum):
    UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
    FILE_SIGNATURE_MISMATCH = "file_signature_mismatch"
    UNSUPPORTED_TEXT_ENCODING = "unsupported_text_encoding"
    DOCUMENT_EMPTY = "document_empty"
    DOCUMENT_CORRUPTED = "document_corrupted"
    DOCUMENT_ENCRYPTED = "document_encrypted"
    RESOURCE_LIMIT_EXCEEDED = "resource_limit_exceeded"
    PARSE_TIMEOUT = "parse_timeout"
    PARSE_FAILED = "parse_failed"
    MODEL_ASSET_MISSING = "model_asset_missing"
    MODEL_ASSET_CHECKSUM_MISMATCH = "model_asset_checksum_mismatch"
    MODEL_ASSET_ROLE_MISMATCH = "model_asset_role_mismatch"
    MODEL_MANIFEST_INVALID = "model_manifest_invalid"


class IngestionError(Exception):
    """Safe parser failure suitable for returning through the API boundary."""

    def __init__(self, code: IngestionErrorCode, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


__all__ = ["IngestionError", "IngestionErrorCode"]
