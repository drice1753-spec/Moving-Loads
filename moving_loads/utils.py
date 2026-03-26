"""Utility functions for moving load analysis."""

from __future__ import annotations
import numpy as np


def interpolate_linear(x: float, x_data: np.ndarray, y_data: np.ndarray) -> float:
    """Linear interpolation of y at position x given data arrays.

    Args:
        x: Position to interpolate at.
        x_data: Array of x-coordinates (must be sorted ascending).
        y_data: Array of y-coordinates.

    Returns:
        Interpolated y value.
    """
    if len(x_data) != len(y_data):
        raise ValueError("x_data and y_data must have the same length.")
    if len(x_data) < 2:
        raise ValueError("At least two data points are required.")
    if x < x_data[0] or x > x_data[-1]:
        raise ValueError(f"x={x} is outside the data range [{x_data[0]}, {x_data[-1]}].")

    idx = np.searchsorted(x_data, x) - 1
    idx = max(0, min(idx, len(x_data) - 2))

    x0, x1 = x_data[idx], x_data[idx + 1]
    y0, y1 = y_data[idx], y_data[idx + 1]

    if x1 == x0:
        return y0

    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)


def compute_resultant(forces: list[tuple[float, float]]) -> tuple[float, float]:
    """Compute the resultant of a set of forces.

    Args:
        forces: List of (position, magnitude) tuples.

    Returns:
        Tuple of (resultant_position, total_magnitude).
    """
    if not forces:
        return (0.0, 0.0)
    total = sum(mag for _, mag in forces)
    if total == 0:
        return (0.0, 0.0)
    position = sum(pos * mag for pos, mag in forces) / total
    return (position, total)


def find_max_index(values: np.ndarray) -> int:
    """Find the index of the maximum value in an array."""
    return int(np.argmax(values))


def find_min_index(values: np.ndarray) -> int:
    """Find the index of the minimum value in an array."""
    return int(np.argmin(values))


def format_results_table(
    positions: np.ndarray,
    shear: np.ndarray,
    moment: np.ndarray,
) -> str:
    """Format analysis results as a text table.

    Args:
        positions: Array of positions along the beam.
        shear: Array of shear values.
        moment: Array of moment values.

    Returns:
        Formatted string table.
    """
    lines = [
        f"{'Position':>10} {'Shear':>12} {'Moment':>12}",
        "-" * 36,
    ]
    for i in range(len(positions)):
        lines.append(f"{positions[i]:>10.3f} {shear[i]:>12.3f} {moment[i]:>12.3f}")
    return "\n".join(lines)
