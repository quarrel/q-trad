#!/usr/bin/env -S uv run --script
"""Run the bounded R3.H exploratory fixture or terminal-retained path."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import tempfile
from pathlib import Path

# Allow direct execution from a checkout without adding a runtime dependency or changing imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from qtrad.application.r3_historical_exploratory import (
    FreezeConfig,
    analyse_fixture,
    fixture_from_json,
    load_fixture_rows,
    load_retained_rows,
    render_markdown,
    synthetic_fixture,
)


def _write_create_only(destination: Path, rendered: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = rendered.encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError as exc:
            raise FileExistsError(f"create-only report already exists: {destination}") from exc
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default="docs/archive/r3/r3-historical-exploratory-freeze.json",
        help="immutable R3.H configuration",
    )
    parser.add_argument("--fixture", help="JSON fixture rows; defaults to the synthetic fixture")
    parser.add_argument(
        "--retained", action="store_true", help="authenticate and load exact retained children"
    )
    parser.add_argument("--selection")
    parser.add_argument("--consumed")
    parser.add_argument("--local-forecast")
    parser.add_argument("--pooled-forecast")
    parser.add_argument("--zero-forecast")
    parser.add_argument("--outcome-evidence")
    parser.add_argument("--output", help="create-only report destination")
    parser.add_argument(
        "--output-format",
        choices=("json", "markdown"),
        default="json",
        help="explicit report encoding; never inferred from the destination name",
    )
    args = parser.parse_args()

    config = FreezeConfig.from_path(args.config)
    retained_metadata = None
    if args.retained:
        locator_values = {
            "selection": args.selection,
            "consumed": args.consumed,
            "local_forecast": args.local_forecast,
            "pooled_forecast": args.pooled_forecast,
            "zero_forecast": args.zero_forecast,
            "outcome_evidence": args.outcome_evidence,
        }
        if any(value is None for value in locator_values.values()):
            parser.error("--retained requires all exact child locator arguments")
        rows, retained_metadata = load_retained_rows(
            config, locators={key: str(value) for key, value in locator_values.items()}
        )
    else:
        fixture_rows = fixture_from_json(args.fixture) if args.fixture else synthetic_fixture()
        rows, retained_metadata = load_fixture_rows(fixture_rows, config)
    result = analyse_fixture(rows, config, retained_metadata=retained_metadata)
    rendered = (
        result.canonical_json() if args.output_format == "json" else render_markdown(result, config)
    )
    if args.output:
        _write_create_only(Path(args.output), rendered)
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
