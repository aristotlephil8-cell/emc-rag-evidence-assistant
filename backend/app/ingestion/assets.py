from __future__ import annotations

import hashlib
import hmac
import json
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.ingestion.errors import IngestionError, IngestionErrorCode
from app.ingestion.versions import DEEPDOC_REVISION

DEEPDOC_MANIFEST_PATH = Path(__file__).with_name("deepdoc_assets_manifest.json")

_ONNX_ROLES = frozenset(
    {
        "ocr_detection",
        "ocr_recognition",
        "layout_detection",
        "table_structure_detection",
    }
)
_REQUIRED_ROLES = _ONNX_ROLES | {"ocr_charset"}


class _OnnxValueInfo(Protocol):
    name: str
    shape: Sequence[object]
    type: str


class _OnnxSession(Protocol):
    def get_inputs(self) -> Sequence[_OnnxValueInfo]: ...

    def get_outputs(self) -> Sequence[_OnnxValueInfo]: ...


@dataclass(frozen=True)
class DeepDocAssetSpec:
    filename: str
    role: str
    size_bytes: int
    sha256: str
    source_revision: str
    source_url: str
    license: str
    role_check: Mapping[str, Any]


@dataclass(frozen=True)
class DeepDocAssetManifest:
    schema_version: int
    asset_set: str
    source_repository: str
    source_revision: str
    license: str
    assets: tuple[DeepDocAssetSpec, ...]


@dataclass(frozen=True)
class DeepDocAssetBundle:
    asset_set: str
    root: Path
    paths_by_role: Mapping[str, Path]

    def path_for(self, role: str) -> Path:
        try:
            return self.paths_by_role[role]
        except KeyError as exc:
            raise ValueError(f"unknown DeepDOC asset role: {role}") from exc


OnnxSessionFactory = Callable[[Path], _OnnxSession]


def _manifest_error(message: str, cause: Exception | None = None) -> None:
    error = IngestionError(IngestionErrorCode.MODEL_MANIFEST_INVALID, message)
    if cause is None:
        raise error
    raise error from cause


def _parse_spec(raw: object) -> DeepDocAssetSpec:
    if not isinstance(raw, dict):
        _manifest_error("DeepDOC asset manifest is invalid")
    try:
        return DeepDocAssetSpec(
            filename=raw["filename"],
            role=raw["role"],
            size_bytes=raw["size_bytes"],
            sha256=raw["sha256"],
            source_revision=raw["source_revision"],
            source_url=raw["source_url"],
            license=raw["license"],
            role_check=raw["role_check"],
        )
    except (KeyError, TypeError) as exc:
        _manifest_error("DeepDOC asset manifest is invalid", exc)


def _validate_manifest(manifest: DeepDocAssetManifest) -> None:
    if (
        type(manifest.schema_version) is not int
        or manifest.schema_version != 1
        or not manifest.asset_set
        or manifest.source_repository != "InfiniFlow/deepdoc"
        or manifest.source_revision != DEEPDOC_REVISION
        or manifest.license != "Apache-2.0"
        or len(manifest.assets) != 5
    ):
        _manifest_error("DeepDOC asset manifest is invalid")

    filenames: set[str] = set()
    roles: set[str] = set()
    for spec in manifest.assets:
        filename = Path(spec.filename)
        valid_filename = (
            bool(spec.filename)
            and filename.name == spec.filename
            and not filename.is_absolute()
            and "/" not in spec.filename
            and "\\" not in spec.filename
        )
        valid_hash = len(spec.sha256) == 64 and all(
            character in "0123456789abcdef" for character in spec.sha256
        )
        valid_source = (
            spec.source_revision == DEEPDOC_REVISION
            and spec.source_url.startswith("https://huggingface.co/InfiniFlow/deepdoc/")
            and DEEPDOC_REVISION in spec.source_url
        )
        if (
            not valid_filename
            or type(spec.size_bytes) is not int
            or spec.size_bytes <= 0
            or not valid_hash
            or not valid_source
            or spec.license != "Apache-2.0"
            or spec.role not in _REQUIRED_ROLES
            or not isinstance(spec.role_check, Mapping)
            or spec.filename in filenames
            or spec.role in roles
        ):
            _manifest_error("DeepDOC asset manifest is invalid")
        filenames.add(spec.filename)
        roles.add(spec.role)
    if roles != _REQUIRED_ROLES:
        _manifest_error("DeepDOC asset manifest is invalid")


def load_deepdoc_manifest(
    manifest_path: Path = DEEPDOC_MANIFEST_PATH,
) -> DeepDocAssetManifest:
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = DeepDocAssetManifest(
            schema_version=raw["schema_version"],
            asset_set=raw["asset_set"],
            source_repository=raw["source_repository"],
            source_revision=raw["source_revision"],
            license=raw["license"],
            assets=tuple(_parse_spec(item) for item in raw["assets"]),
        )
    except IngestionError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        _manifest_error("DeepDOC asset manifest is invalid", exc)
    _validate_manifest(manifest)
    return manifest


def _asset_path(root_input: Path, root: Path, spec: DeepDocAssetSpec) -> Path:
    candidate = root_input / spec.filename
    try:
        candidate_stat = candidate.lstat()
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise IngestionError(
            IngestionErrorCode.MODEL_ASSET_MISSING,
            f"DeepDOC model asset is missing: {spec.filename}",
        ) from exc
    except OSError as exc:
        raise IngestionError(
            IngestionErrorCode.MODEL_ASSET_ROLE_MISMATCH,
            f"DeepDOC model asset cannot be inspected: {spec.filename}",
        ) from exc
    if stat.S_ISLNK(candidate_stat.st_mode) or not stat.S_ISREG(candidate_stat.st_mode):
        raise IngestionError(
            IngestionErrorCode.MODEL_ASSET_ROLE_MISMATCH,
            f"DeepDOC model asset location is invalid: {spec.filename}",
        )
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise IngestionError(
            IngestionErrorCode.MODEL_ASSET_ROLE_MISMATCH,
            f"DeepDOC model asset location is invalid: {spec.filename}",
        ) from exc
    return resolved


def _validate_hash(path: Path, spec: DeepDocAssetSpec) -> None:
    digest = hashlib.sha256()
    try:
        size = path.stat().st_size
        with path.open("rb") as asset_file:
            while chunk := asset_file.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise IngestionError(
            IngestionErrorCode.MODEL_ASSET_ROLE_MISMATCH,
            f"DeepDOC model asset cannot be read: {spec.filename}",
        ) from exc
    if size != spec.size_bytes or not hmac.compare_digest(digest.hexdigest(), spec.sha256):
        raise IngestionError(
            IngestionErrorCode.MODEL_ASSET_CHECKSUM_MISMATCH,
            f"DeepDOC model asset checksum mismatch: {spec.filename}",
        )


def _default_session_factory(path: Path) -> _OnnxSession:
    import onnxruntime

    options = onnxruntime.SessionOptions()
    options.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_DISABLE_ALL
    return onnxruntime.InferenceSession(
        str(path),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )


def _shape_matches(actual: Sequence[object], expected: object) -> bool:
    if not isinstance(expected, list) or len(actual) != len(expected):
        return False
    return all(
        expected_dimension is None or actual_dimension == expected_dimension
        for actual_dimension, expected_dimension in zip(actual, expected, strict=True)
    )


def _validate_onnx_role(
    path: Path,
    spec: DeepDocAssetSpec,
    session_factory: OnnxSessionFactory,
) -> None:
    try:
        session = session_factory(path)
        inputs = tuple(session.get_inputs())
        outputs = tuple(session.get_outputs())
        checks = spec.role_check
        compatible = (
            len(inputs) == 1
            and len(outputs) == 1
            and inputs[0].name == checks.get("input_name")
            and inputs[0].type == checks.get("input_type")
            and _shape_matches(inputs[0].shape, checks.get("input_shape"))
            and outputs[0].name == checks.get("output_name")
            and outputs[0].type == checks.get("output_type")
            and _shape_matches(outputs[0].shape, checks.get("output_shape"))
        )
    except MemoryError:
        raise
    except Exception as exc:
        raise IngestionError(
            IngestionErrorCode.MODEL_ASSET_ROLE_MISMATCH,
            f"DeepDOC model asset role mismatch: {spec.filename}",
        ) from exc
    if not compatible:
        raise IngestionError(
            IngestionErrorCode.MODEL_ASSET_ROLE_MISMATCH,
            f"DeepDOC model asset role mismatch: {spec.filename}",
        )


def _validate_charset(path: Path, spec: DeepDocAssetSpec) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        expected_count = spec.role_check["utf8_line_count"]
    except (OSError, UnicodeError, KeyError, TypeError) as exc:
        raise IngestionError(
            IngestionErrorCode.MODEL_ASSET_ROLE_MISMATCH,
            f"DeepDOC model asset role mismatch: {spec.filename}",
        ) from exc
    if len(lines) != expected_count or any(not line for line in lines):
        raise IngestionError(
            IngestionErrorCode.MODEL_ASSET_ROLE_MISMATCH,
            f"DeepDOC model asset role mismatch: {spec.filename}",
        )


def validate_deepdoc_assets(
    model_dir: Path,
    *,
    manifest: DeepDocAssetManifest | None = None,
    onnx_session_factory: OnnxSessionFactory | None = None,
    validate_roles: bool = True,
) -> DeepDocAssetBundle:
    """Fail closed on missing, changed, or role-incompatible model assets."""

    manifest = manifest or load_deepdoc_manifest()
    _validate_manifest(manifest)
    try:
        if model_dir.is_symlink():
            raise ValueError("model directory cannot be a symbolic link")
        root = model_dir.resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError
    except FileNotFoundError as exc:
        raise IngestionError(
            IngestionErrorCode.MODEL_ASSET_MISSING,
            "DeepDOC model directory is missing",
        ) from exc
    except (OSError, ValueError) as exc:
        raise IngestionError(
            IngestionErrorCode.MODEL_ASSET_ROLE_MISMATCH,
            "DeepDOC model directory is invalid",
        ) from exc

    paths_by_role: dict[str, Path] = {}
    for spec in manifest.assets:
        path = _asset_path(model_dir, root, spec)
        _validate_hash(path, spec)
        paths_by_role[spec.role] = path

    if validate_roles:
        session_factory = onnx_session_factory or _default_session_factory
        for spec in manifest.assets:
            path = paths_by_role[spec.role]
            if spec.role in _ONNX_ROLES:
                _validate_onnx_role(path, spec, session_factory)
            else:
                _validate_charset(path, spec)

    return DeepDocAssetBundle(
        asset_set=manifest.asset_set,
        root=root,
        paths_by_role=paths_by_role,
    )


__all__ = [
    "DEEPDOC_MANIFEST_PATH",
    "DEEPDOC_REVISION",
    "DeepDocAssetBundle",
    "DeepDocAssetManifest",
    "DeepDocAssetSpec",
    "load_deepdoc_manifest",
    "validate_deepdoc_assets",
]
