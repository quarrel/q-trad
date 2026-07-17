#!/usr/bin/env python3
"""Verify q-trad provider-stream experiment evidence without provider access."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

_CONTRAST = "IG_SINGLE_CONNECTION_PRICE_CHART_TICK_CONTRAST"
_RECOVERY = "IG_QTRAD_STREAM_AND_TOKEN_RECOVERY"


def _parse_args() -> Path:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    return cast(Path, parser.parse_args().manifest)


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise TypeError(f"{context} must be a JSON object with string keys")
    return cast(dict[str, object], value)


def _text(value: Mapping[str, object], key: str) -> str:
    candidate = value[key]
    if not isinstance(candidate, str) or not candidate:
        raise TypeError(f"{key} must be a non-empty string")
    return candidate


def _integer(value: Mapping[str, object], key: str) -> int:
    candidate = value[key]
    if not isinstance(candidate, int) or isinstance(candidate, bool) or candidate < 0:
        raise TypeError(f"{key} must be a non-negative integer")
    return candidate


def _verify_contrast(manifest_path: Path, manifest: Mapping[str, object]) -> dict[str, object]:
    stream = _object(manifest["event_stream"], "event_stream")
    if _text(stream, "encoding") != "gzip-json-lines":
        raise ValueError("unsupported contrast event encoding")
    relative = Path(_text(stream, "path"))
    if relative.is_absolute():
        raise ValueError("contrast event path must be relative to its manifest")
    manifest_parent = manifest_path.resolve().parent
    event_path = (manifest_parent / relative).resolve()
    if event_path.parent != manifest_parent:
        raise ValueError("contrast event path must remain beside its manifest")

    digest = hashlib.sha256()
    count = 0
    previous_sequence = 0
    with gzip.open(event_path, "rb") as handle:
        for line in handle:
            digest.update(line)
            record = _object(json.loads(line), f"event {count + 1}")
            sequence = _integer(record, "sequence")
            if sequence <= previous_sequence:
                raise ValueError("contrast event sequences must be strictly increasing")
            previous_sequence = sequence
            count += 1
    if count != _integer(stream, "record_count"):
        raise ValueError("contrast event record count does not match its manifest")
    if digest.hexdigest() != _text(stream, "uncompressed_sha256"):
        raise ValueError("contrast event stream hash does not match its manifest")
    return {
        "event_path": event_path.name,
        "event_records": count,
        "event_sha256": digest.hexdigest(),
    }


def verify(manifest_path: Path) -> dict[str, object]:
    manifest = _object(json.loads(manifest_path.read_text(encoding="utf-8")), "manifest")
    expected_hash = _text(manifest, "evidence_sha256")
    unsigned = dict(manifest)
    del unsigned["evidence_sha256"]
    actual_hash = _canonical_hash(unsigned)
    if actual_hash != expected_hash:
        raise ValueError("experiment manifest self-hash does not match")

    experiment = _text(manifest, "experiment")
    detail: dict[str, object] = {}
    if experiment == _CONTRAST:
        detail = _verify_contrast(manifest_path, manifest)
    elif experiment != _RECOVERY:
        raise ValueError(f"unsupported stream experiment: {experiment}")
    result = _text(manifest, "result")
    if result not in {"PASS", "FAIL"}:
        raise ValueError("experiment result must be PASS or FAIL")
    return {
        "verified": True,
        "experiment": experiment,
        "result": result,
        "evidence_sha256": actual_hash,
        **detail,
    }


def main() -> None:
    result = verify(_parse_args())
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
