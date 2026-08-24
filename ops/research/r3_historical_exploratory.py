#!/usr/bin/env -S uv run --script
"""Run the bounded R3.H exploratory fixture or terminal-retained path."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow direct execution from a checkout without adding a runtime dependency or changing imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from qtrad.application.r3_historical_exploratory import (
    FreezeConfig,
    analyse_fixture,
    fixture_from_json,
    load_retained_rows,
    synthetic_fixture,
)


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
        rows = fixture_from_json(args.fixture) if args.fixture else synthetic_fixture()
    result = analyse_fixture(rows, config, retained_metadata=retained_metadata)
    rendered = result.canonical_json()
    if args.output:
        destination = Path(args.output)
        try:
            with destination.open("x", encoding="utf-8") as handle:
                handle.write(rendered)
        except FileExistsError as exc:
            raise FileExistsError(f"create-only report already exists: {destination}") from exc
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
