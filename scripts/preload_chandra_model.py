from __future__ import annotations

import argparse
import json
from pathlib import Path

from huggingface_hub import snapshot_download


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and pin a local Chandra model snapshot for offline Docker images."
    )
    parser.add_argument(
        "--model-id",
        default="datalab-to/chandra-ocr-2",
        help="Hugging Face model repository id.",
    )
    parser.add_argument(
        "--target-dir",
        required=True,
        help="Directory where the model snapshot will be materialized.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    target_dir = Path(args.target_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    snapshot_path = snapshot_download(
        repo_id=args.model_id,
        local_dir=str(target_dir),
    )

    metadata = {
        "model_id": args.model_id,
        "snapshot_path": snapshot_path,
        "target_dir": str(target_dir),
    }
    (target_dir / "download-metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
