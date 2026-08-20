from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import time
from pathlib import Path

from huggingface_hub import hf_hub_download

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.ingestion.assets import (  # noqa: E402
    DEEPDOC_REVISION,
    DeepDocAssetSpec,
    load_deepdoc_manifest,
    validate_deepdoc_assets,
)


def _matches(path: Path, spec: DeepDocAssetSpec) -> bool:
    if not path.is_file() or path.is_symlink() or path.stat().st_size != spec.size_bytes:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as asset_file:
        while chunk := asset_file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest() == spec.sha256


def prepare(model_dir: Path, *, offline: bool = False) -> None:
    manifest = load_deepdoc_manifest()
    model_dir.mkdir(parents=True, exist_ok=True)
    if model_dir.is_symlink():
        raise RuntimeError("model directory cannot be a symbolic link")

    for spec in manifest.assets:
        destination = model_dir / spec.filename
        if _matches(destination, spec):
            print(f"verified existing {spec.filename}")
            continue
        cached: Path | None = None
        for attempt in range(3):
            try:
                cached = Path(
                    hf_hub_download(
                        repo_id="InfiniFlow/deepdoc",
                        filename=spec.filename,
                        revision=DEEPDOC_REVISION,
                        repo_type="model",
                        local_files_only=offline,
                    )
                )
                break
            except Exception:
                if offline or attempt == 2:
                    raise
                time.sleep(2**attempt)
        if cached is None:
            raise RuntimeError(f"asset download did not return a cache path: {spec.filename}")
        staged = model_dir / f".{spec.filename}.partial"
        try:
            with cached.open("rb") as source, staged.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            if not _matches(staged, spec):
                raise RuntimeError(f"downloaded asset failed SHA-256 validation: {spec.filename}")
            os.replace(staged, destination)
            print(f"prepared {spec.filename}")
        finally:
            staged.unlink(missing_ok=True)

    bundle = validate_deepdoc_assets(model_dir)
    print(
        f"DeepDOC asset set {bundle.asset_set} is ready at {bundle.root} "
        f"(revision {DEEPDOC_REVISION})"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download and verify the pinned InfiniFlow DeepDOC assets.",
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        required=True,
        help="Destination directory excluded from Git.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use only the existing Hugging Face cache.",
    )
    args = parser.parse_args()
    prepare(args.model_dir, offline=args.offline)


if __name__ == "__main__":
    main()
