"""Conservative historical-data allowance calculation."""

from math import floor


def points_per_instrument(
    *, remaining_allowance: int, instrument_count: int = 7, maximum_points: int = 1000
) -> int:
    if remaining_allowance < 0:
        raise ValueError("remaining allowance must not be negative")
    if instrument_count <= 0 or maximum_points <= 0:
        raise ValueError("instrument count and maximum points must be positive")
    return min(maximum_points, floor(0.8 * remaining_allowance / instrument_count))
