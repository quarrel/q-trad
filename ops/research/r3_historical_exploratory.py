#!/usr/bin/env -S uv run --script
"""Run the bounded R3.H exploratory fixture path.

The command intentionally performs no retained authentication. It is the exact production-like
entry path used by the Stage 1 micro-run; a later authorised consumer may authenticate the named
Stage 7 receipt once before loading required children.
"""

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
    synthetic_fixture,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default="docs/archive/r3/r3-historical-exploratory-freeze.json",
        help="immutable Stage 1 configuration",
    )
    parser.add_argument("--fixture", help="JSON fixture rows; defaults to the synthetic fixture")
    parser.add_argument("--output", help="create-only report destination")
    args = parser.parse_args()

    config = FreezeConfig.from_path(args.config)
    rows = fixture_from_json(args.fixture) if args.fixture else synthetic_fixture()
    result = analyse_fixture(rows, config)
    rendered = result.canonical_json()
    if args.output:
        destination = Path(args.output)
        if destination.exists():
            raise FileExistsError(f"create-only report already exists: {destination}")
        destination.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
