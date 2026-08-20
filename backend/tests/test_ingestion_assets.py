from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.ingestion.assets import (
    DEEPDOC_REVISION,
    DeepDocAssetManifest,
    DeepDocAssetSpec,
    load_deepdoc_manifest,
    validate_deepdoc_assets,
)
from app.ingestion.errors import IngestionError, IngestionErrorCode

_ROLES = (
    ("det.onnx", "ocr_detection"),
    ("rec.onnx", "ocr_recognition"),
    ("ocr.res", "ocr_charset"),
    ("layout.onnx", "layout_detection"),
    ("tsr.onnx", "table_structure_detection"),
)


def _test_manifest(contents: dict[str, bytes]) -> DeepDocAssetManifest:
    specs: list[DeepDocAssetSpec] = []
    for filename, role in _ROLES:
        role_check = (
            {"utf8_line_count": 1}
            if role == "ocr_charset"
            else {
                "input_name": "input",
                "input_type": "tensor(float)",
                "input_shape": [None, 3],
                "output_name": "output",
                "output_type": "tensor(float)",
                "output_shape": [None, 4],
            }
        )
        data = contents[filename]
        specs.append(
            DeepDocAssetSpec(
                filename=filename,
                role=role,
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                source_revision=DEEPDOC_REVISION,
                source_url=(
                    "https://huggingface.co/InfiniFlow/deepdoc/resolve/"
                    f"{DEEPDOC_REVISION}/{filename}"
                ),
                license="Apache-2.0",
                role_check=role_check,
            )
        )
    return DeepDocAssetManifest(
        schema_version=1,
        asset_set="unit-test-assets",
        source_repository="InfiniFlow/deepdoc",
        source_revision=DEEPDOC_REVISION,
        license="Apache-2.0",
        assets=tuple(specs),
    )


def _write_assets(root: Path, contents: dict[str, bytes]) -> None:
    root.mkdir()
    for filename, data in contents.items():
        (root / filename).write_bytes(data)


def _contents() -> dict[str, bytes]:
    return {
        "det.onnx": b"det",
        "rec.onnx": b"rec",
        "ocr.res": b"A\n",
        "layout.onnx": b"layout",
        "tsr.onnx": b"table",
    }


def test_pinned_manifest_has_five_hashed_assets() -> None:
    manifest = load_deepdoc_manifest()

    assert manifest.source_revision == DEEPDOC_REVISION
    assert len(manifest.assets) == 5
    assert {asset.role for asset in manifest.assets} == {role for _, role in _ROLES}
    assert all(len(asset.sha256) == 64 for asset in manifest.assets)
    assert all(asset.license == "Apache-2.0" for asset in manifest.assets)


def test_asset_hashes_are_validated(tmp_path: Path) -> None:
    contents = _contents()
    root = tmp_path / "models"
    _write_assets(root, contents)
    manifest = _test_manifest(contents)

    bundle = validate_deepdoc_assets(root, manifest=manifest, validate_roles=False)
    assert bundle.path_for("ocr_detection") == (root / "det.onnx").resolve()

    (root / "det.onnx").write_bytes(b"changed")
    with pytest.raises(IngestionError) as caught:
        validate_deepdoc_assets(root, manifest=manifest, validate_roles=False)
    assert caught.value.code == IngestionErrorCode.MODEL_ASSET_CHECKSUM_MISMATCH


def test_missing_asset_is_explicit(tmp_path: Path) -> None:
    root = tmp_path / "models"
    root.mkdir()
    with pytest.raises(IngestionError) as caught:
        validate_deepdoc_assets(root)
    assert caught.value.code == IngestionErrorCode.MODEL_ASSET_MISSING
    assert "det.onnx" in caught.value.safe_message


@dataclass
class _ValueInfo:
    name: str
    type: str
    shape: list[object]


class _WrongRoleSession:
    def get_inputs(self) -> list[_ValueInfo]:
        return [_ValueInfo(name="wrong", type="tensor(float)", shape=[None, 3])]

    def get_outputs(self) -> list[_ValueInfo]:
        return [_ValueInfo(name="output", type="tensor(float)", shape=[None, 4])]


def test_onnx_role_mismatch_is_explicit(tmp_path: Path) -> None:
    contents = _contents()
    root = tmp_path / "models"
    _write_assets(root, contents)
    manifest = _test_manifest(contents)

    with pytest.raises(IngestionError) as caught:
        validate_deepdoc_assets(
            root,
            manifest=manifest,
            onnx_session_factory=lambda _: _WrongRoleSession(),
        )
    assert caught.value.code == IngestionErrorCode.MODEL_ASSET_ROLE_MISMATCH
