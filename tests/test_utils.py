"""Tests for the utils module."""

import numpy as np
import pytest
from moving_loads.utils import (
    interpolate_linear,
    compute_resultant,
    find_max_index,
    find_min_index,
    format_results_table,
)


class TestInterpolateLinear:
    """Tests for linear interpolation."""

    def test_interpolate_at_data_point(self):
        x_data = np.array([0.0, 1.0, 2.0])
        y_data = np.array([0.0, 10.0, 20.0])
        assert interpolate_linear(1.0, x_data, y_data) == pytest.approx(10.0)

    def test_interpolate_between_points(self):
        x_data = np.array([0.0, 2.0, 4.0])
        y_data = np.array([0.0, 10.0, 20.0])
        assert interpolate_linear(1.0, x_data, y_data) == pytest.approx(5.0)

    def test_interpolate_at_boundaries(self):
        x_data = np.array([0.0, 5.0, 10.0])
        y_data = np.array([1.0, 3.0, 7.0])
        assert interpolate_linear(0.0, x_data, y_data) == pytest.approx(1.0)
        assert interpolate_linear(10.0, x_data, y_data) == pytest.approx(7.0)

    def test_out_of_range_raises(self):
        x_data = np.array([0.0, 1.0, 2.0])
        y_data = np.array([0.0, 1.0, 2.0])
        with pytest.raises(ValueError, match="outside the data range"):
            interpolate_linear(-1.0, x_data, y_data)
        with pytest.raises(ValueError, match="outside the data range"):
            interpolate_linear(3.0, x_data, y_data)

    def test_mismatched_lengths_raises(self):
        with pytest.raises(ValueError, match="same length"):
            interpolate_linear(0.5, np.array([0.0, 1.0]), np.array([0.0]))

    def test_insufficient_data_raises(self):
        with pytest.raises(ValueError, match="At least two"):
            interpolate_linear(0.5, np.array([1.0]), np.array([1.0]))


class TestComputeResultant:
    """Tests for resultant force computation."""

    def test_single_force(self):
        pos, mag = compute_resultant([(5.0, 100.0)])
        assert pos == pytest.approx(5.0)
        assert mag == pytest.approx(100.0)

    def test_two_equal_forces(self):
        pos, mag = compute_resultant([(0.0, 50.0), (10.0, 50.0)])
        assert pos == pytest.approx(5.0)
        assert mag == pytest.approx(100.0)

    def test_asymmetric_forces(self):
        pos, mag = compute_resultant([(0.0, 100.0), (10.0, 300.0)])
        assert pos == pytest.approx(7.5)
        assert mag == pytest.approx(400.0)

    def test_empty_forces(self):
        pos, mag = compute_resultant([])
        assert mag == pytest.approx(0.0)

    def test_zero_magnitude_forces(self):
        pos, mag = compute_resultant([(5.0, 0.0), (10.0, 0.0)])
        assert mag == pytest.approx(0.0)


class TestFindMaxMinIndex:
    """Tests for max/min index finders."""

    def test_find_max_index(self):
        assert find_max_index(np.array([1, 5, 3, 2])) == 1

    def test_find_min_index(self):
        assert find_min_index(np.array([4, 1, 3, 2])) == 1

    def test_single_element(self):
        assert find_max_index(np.array([42])) == 0
        assert find_min_index(np.array([42])) == 0

    def test_ties_returns_first(self):
        assert find_max_index(np.array([5, 5, 1])) == 0
        assert find_min_index(np.array([1, 1, 5])) == 0


class TestFormatResultsTable:
    """Tests for results table formatting."""

    def test_table_has_header(self):
        positions = np.array([0.0, 5.0])
        shear = np.array([10.0, -10.0])
        moment = np.array([0.0, 25.0])
        table = format_results_table(positions, shear, moment)
        assert "Position" in table
        assert "Shear" in table
        assert "Moment" in table

    def test_table_has_correct_row_count(self):
        positions = np.array([0.0, 5.0, 10.0])
        shear = np.array([10.0, 0.0, -10.0])
        moment = np.array([0.0, 25.0, 0.0])
        table = format_results_table(positions, shear, moment)
        lines = table.strip().split("\n")
        # 2 header lines + 3 data lines
        assert len(lines) == 5

    def test_table_contains_values(self):
        positions = np.array([2.5])
        shear = np.array([12.345])
        moment = np.array([67.890])
        table = format_results_table(positions, shear, moment)
        assert "2.500" in table
        assert "12.345" in table
        assert "67.890" in table
