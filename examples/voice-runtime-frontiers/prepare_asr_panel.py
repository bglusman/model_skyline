#!/usr/bin/env python3
"""Prepare a small deterministic Open ASR Leaderboard panel without playback.

Audio remains in the operator-selected local directory. The generated manifest
retains exact dataset identity, source row IDs, transcripts, and content hashes
so benchmark captures can be reproduced without committing voice recordings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

DATASET_ID = "hf-audio/open-asr-leaderboard"
DATASET_REVISION = "b6bdcd0beb34f8975dc659796176d88f43aff502"
PANEL_ID = "local-asr-pilot-v1"
SELECTION_SALT = "model-skyline:local-asr-pilot-v1"
BASE_URL = f"https://huggingface.co/datasets/{DATASET_ID}/resolve/{DATASET_REVISION}"


@dataclass(frozen=True)
class Source:
    config: str
    domain: str
    split: str
    shards: tuple[str, ...]
    license: str
    source_url: str
    notes: str


SOURCES = (
    Source(
        config="ami_cleaned",
        domain="spontaneous_meeting",
        split="test",
        shards=tuple(f"ami_cleaned/test-{index:05d}-of-00003.parquet" for index in range(3)),
        license="CC-BY-4.0",
        source_url="https://groups.inf.ed.ac.uk/ami/",
        notes="Error-corrected AMI meeting references from the leaderboard package.",
    ),
    Source(
        config="earnings22",
        domain="financial_call",
        split="test",
        shards=tuple(f"earnings22/test-{index:05d}-of-00005.parquet" for index in range(5)),
        license="CC-BY-SA-4.0",
        source_url="https://github.com/revdotcom/speech-datasets/tree/main/earnings22",
        notes="Spontaneous global-company earnings calls with domain terminology.",
    ),
    Source(
        config="librispeech_test_other",
        domain="audiobook_other",
        split="test.other",
        shards=("librispeech/test.other-00000-of-00001.parquet",),
        license="CC-BY-4.0",
        source_url="https://www.openslr.org/12",
        notes="The more difficult LibriSpeech test.other narrated-speech split.",
    ),
    Source(
        config="voxpopuli_cleaned_aa",
        domain="non_us_political_oratory",
        split="test",
        shards=("voxpopuli_cleaned_aa/test-00000-of-00001.parquet",),
        license="CC0-1.0",
        source_url="https://github.com/facebookresearch/voxpopuli",
        notes="Human-corrected English VoxPopuli subset used by leaderboard analysis.",
    ),
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.")


def _wav_metadata(path: Path) -> tuple[int, int, float]:
    with wave.open(str(path), "rb") as source:
        sample_rate = source.getframerate()
        channels = source.getnchannels()
        frames = source.getnframes()
        if source.getsampwidth() != 2 or source.getcomptype() != "NONE":
            raise ValueError(f"{path} is not uncompressed 16-bit PCM")
    return sample_rate, channels, frames / sample_rate


def _ffmpeg_version() -> str:
    result = subprocess.run(
        ["ffmpeg", "-version"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()[0]


def _normalize_audio(source_bytes: bytes, source_suffix: str, output: Path) -> None:
    with tempfile.NamedTemporaryFile(suffix=source_suffix) as temporary:
        temporary.write(source_bytes)
        temporary.flush()
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                temporary.name,
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(output),
            ],
            check=True,
        )


def _quoted_urls(source: Source) -> str:
    return (
        "["
        + ",".join("'" + f"{BASE_URL}/{shard}".replace("'", "''") + "'" for shard in source.shards)
        + "]"
    )


def _select_rows(
    connection: duckdb.DuckDBPyConnection,
    source: Source,
    samples_per_source: int,
    minimum_seconds: float,
    maximum_seconds: float,
) -> list[dict[str, Any]]:
    urls = _quoted_urls(source)
    selected = connection.execute(
        f"""
        SELECT id, dataset, text, audio_length_s
        FROM read_parquet({urls})
        WHERE audio_length_s BETWEEN ? AND ?
          AND array_length(string_split(trim(text), ' ')) >= 8
        ORDER BY md5(id || ?)
        LIMIT ?
        """,
        [minimum_seconds, maximum_seconds, SELECTION_SALT, samples_per_source],
    ).fetchall()
    if len(selected) != samples_per_source:
        raise RuntimeError(f"{source.config} yielded {len(selected)} of {samples_per_source} clips")
    selected_ids = [row[0] for row in selected]
    placeholders = ",".join("?" for _ in selected_ids)
    audio_rows = connection.execute(
        f"""
        SELECT id, audio.path, audio.bytes
        FROM read_parquet({urls})
        WHERE id IN ({placeholders})
        """,
        selected_ids,
    ).fetchall()
    audio_by_id = {row[0]: (row[1], bytes(row[2])) for row in audio_rows}
    if set(audio_by_id) != set(selected_ids):
        raise RuntimeError(f"could not resolve every selected {source.config} audio blob")
    return [
        {
            "row_id": row_id,
            "dataset_label": dataset,
            "reference": text,
            "source_duration_seconds": float(audio_length_s),
            "source_path": audio_by_id[row_id][0],
            "source_bytes": audio_by_id[row_id][1],
        }
        for row_id, dataset, text, audio_length_s in selected
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples-per-source", type=int, default=6)
    parser.add_argument("--minimum-seconds", type=float, default=5.0)
    parser.add_argument("--maximum-seconds", type=float, default=15.0)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.samples_per_source <= 0:
        raise SystemExit("--samples-per-source must be positive")
    if not 0 < args.minimum_seconds <= args.maximum_seconds:
        raise SystemExit("duration bounds must be positive and ordered")
    args.audio_dir.mkdir(parents=True, exist_ok=True)

    connection = duckdb.connect()
    items: list[dict[str, Any]] = []
    sequence = 1
    for source in SOURCES:
        rows = _select_rows(
            connection,
            source,
            args.samples_per_source,
            args.minimum_seconds,
            args.maximum_seconds,
        )
        for row in rows:
            filename = f"{sequence:03d}-{source.config}-{_safe_name(row['row_id'])}.wav"
            output_path = args.audio_dir / filename
            source_bytes = row.pop("source_bytes")
            source_suffix = Path(row["source_path"]).suffix or ".audio"
            _normalize_audio(source_bytes, source_suffix, output_path)
            sample_rate, channels, duration = _wav_metadata(output_path)
            items.append(
                {
                    "testcase_id": f"{source.config}:{row['row_id']}",
                    "domain": source.domain,
                    "dataset_config": source.config,
                    "dataset_split": source.split,
                    "source_row_id": row["row_id"],
                    "dataset_label": row["dataset_label"],
                    "reference": row["reference"],
                    "source_audio_path": row["source_path"],
                    "source_audio_sha256": _sha256_bytes(source_bytes),
                    "source_duration_seconds": round(row["source_duration_seconds"], 6),
                    "audio_filename": filename,
                    "audio_sha256": _sha256(output_path),
                    "audio_seconds": round(duration, 6),
                    "sample_rate_hz": sample_rate,
                    "channels": channels,
                }
            )
            sequence += 1

    payload = {
        "schema": "model-skyline/local-asr-prompt-manifest/v1alpha1",
        "id": PANEL_ID,
        "version": "1.0.0",
        "selection": {
            "rule": (
                "per source, clips with 8+ whitespace-delimited reference tokens "
                "and duration within the declared inclusive range, ordered by "
                "md5(source row id plus the fixed selection salt)"
            ),
            "salt": SELECTION_SALT,
            "samples_per_source": args.samples_per_source,
            "minimum_seconds": args.minimum_seconds,
            "maximum_seconds": args.maximum_seconds,
        },
        "source_dataset": {
            "id": DATASET_ID,
            "revision": DATASET_REVISION,
            "url": f"https://huggingface.co/datasets/{DATASET_ID}",
            "methodology_url": "https://github.com/huggingface/open_asr_leaderboard",
            "sources": [
                {
                    "config": source.config,
                    "domain": source.domain,
                    "split": source.split,
                    "shards": list(source.shards),
                    "license": source.license,
                    "source_url": source.source_url,
                    "notes": source.notes,
                }
                for source in SOURCES
            ],
        },
        "normalization": {
            "audio": "FFmpeg mono 16 kHz signed 16-bit little-endian PCM WAV",
            "ffmpeg": _ffmpeg_version(),
            "warning": (
                "Normalized audio is local-only. Reproductions verify source row and "
                "content hashes before comparing model measurements."
            ),
        },
        "items": items,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "manifest": str(args.output),
                "sample_count": len(items),
                "audio_seconds": round(sum(item["audio_seconds"] for item in items), 3),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
