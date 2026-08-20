from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.pipeline import (
    locked_refusal_threshold,
    pipeline_index_version,
    runtime_config_hash,
    runtime_evaluation_config,
)


def _locked_settings(tmp_path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "CVRAG_PROVIDER": "dashscope",
        "CVRAG_REQUIRE_EVALUATION_LOCK": True,
        "CVRAG_EVALUATION_REPORT": tmp_path / "latest.json",
    }
    values.update(overrides)
    return Settings(**values)


def test_passed_runtime_lock_supplies_the_evaluated_threshold(tmp_path) -> None:
    settings = _locked_settings(tmp_path)
    runtime_config = runtime_evaluation_config(settings, 0.65)
    settings.CVRAG_EVALUATION_REPORT.write_text(
        json.dumps(
            {
                "status": "passed",
                "evidence_status": "VERIFIED_SYNTHETIC",
                "runtime_config": runtime_config,
                "config_hash": runtime_config_hash(runtime_config),
            }
        ),
        encoding="utf-8",
    )

    assert locked_refusal_threshold(settings) == 0.65


def test_runtime_lock_rejects_model_drift(tmp_path) -> None:
    evaluated = _locked_settings(tmp_path)
    runtime_config = runtime_evaluation_config(evaluated, 0.5)
    evaluated.CVRAG_EVALUATION_REPORT.write_text(
        json.dumps(
            {
                "status": "passed",
                "evidence_status": "VERIFIED_SYNTHETIC",
                "runtime_config": runtime_config,
                "config_hash": runtime_config_hash(runtime_config),
            }
        ),
        encoding="utf-8",
    )
    drifted = _locked_settings(
        tmp_path,
        GENERATION_MODEL="different-generation-model",
    )

    with pytest.raises(RuntimeError, match="evaluation_lock_config_mismatch"):
        locked_refusal_threshold(drifted)


def test_failed_report_cannot_unlock_dashscope_demo(tmp_path) -> None:
    settings = _locked_settings(tmp_path)
    runtime_config = runtime_evaluation_config(settings, 0.5)
    settings.CVRAG_EVALUATION_REPORT.write_text(
        json.dumps(
            {
                "status": "failed",
                "evidence_status": "VERIFIED_SYNTHETIC",
                "runtime_config": runtime_config,
                "config_hash": runtime_config_hash(runtime_config),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="evaluation_lock_not_passed"):
        locked_refusal_threshold(settings)


def test_parser_bundle_participates_in_index_version(tmp_path, monkeypatch) -> None:
    settings = _locked_settings(tmp_path)
    before = pipeline_index_version(settings)
    monkeypatch.setattr("app.pipeline.PARSER_BUNDLE_VERSION", "parser-bundle-drift")

    assert pipeline_index_version(settings) != before
