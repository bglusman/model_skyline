#!/usr/bin/env python3
"""Hash the exact files that define a directory-backed local model artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath

from model_skyline.canonical import content_hash


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(16 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise argparse.ArgumentTypeError("artifact file paths must be safe relative POSIX paths")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("files", type=_safe_relative, nargs="+")
    args = parser.parse_args()

    root = args.root.resolve(strict=True)
    unique = sorted(set(args.files), key=str)
    entries: list[dict[str, object]] = []
    for relative in unique:
        source = root.joinpath(*relative.parts)
        try:
            resolved = source.resolve(strict=True)
        except OSError as exc:
            parser.error(f"cannot resolve {relative}: {exc}")
        # Hugging Face snapshots normally use file symlinks into their blob
        # store. The lexical path is already constrained beneath root; hash the
        # target bytes but retain only the portable relative path.
        if not source.is_file() or not resolved.is_file():
            parser.error(f"{relative} does not resolve to a regular file")
        stat = resolved.stat()
        entries.append(
            {
                "path": str(relative),
                "size_bytes": stat.st_size,
                "sha256": _file_sha256(resolved),
            }
        )
    payload = {
        "schema_version": "model-skyline/local-artifact-manifest/v1",
        "root_name": root.name,
        "size_bytes": sum(int(entry["size_bytes"]) for entry in entries),
        "content_sha256": content_hash(entries),
        "digest_scheme": "sha256-rfc8785-file-manifest-v1",
        "files": entries,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
