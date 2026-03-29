"""Tests for the analysis module."""

import numpy as np
import pytest
from moving_loads.beam import Beam
from moving_loads.load import PointLoad, LoadTrain, hl93_design_truck
from moving_loads.analysis import (
    influence_line_reaction,
    influence_line_shear,
    influence_line_moment,
    moving_load_envelope,
    critical_positions,
    compute_deflection_envelope,
)


class TestInfluenceLines:
    """Tests for influence line computations."""

    def test_reaction_a_influence_line_endpoints(self):
        beam = Beam(length=10.0)
        positions, values = influence_line_reaction(beam, 'A', num_points=11)
        assert values[0] == pytest.approx(1.0)
        assert values[-1] == pytest.approx(0.0)

    def test_reaction_b_influence_line_endpoints(self):
        beam = Beam(length=10.0)
        positions, values = influence_line_reaction(beam, 'B', num_points=11)
        assert values[0] == pytest.approx(0.0)
        assert values[-1] == pytest.approx(1.0)

    def test_moment_influence_line_midspan(self):
        beam = Beam(length=10.0)
        positions, values = influence_line_moment(beam, 5.0, num_points=11)
        # Max moment influence at midspan when load is at midspan = L/4 = 2.5
        assert values[5] == pytest.approx(2.5)

    def test_invalid_support_raises(self):
        beam = Beam(length=10.0)
        with pytest.raises(ValueError):
            influence_line_reaction(beam, 'C')

    def test_shear_influence_line_shape(self):
        beam = Beam(length=10.0)
        positions, values = influence_line_shear(beam, 5.0, num_points=11)
        # Load left of x=5: shear = Ra - P = (L-pos)/L - 1 = -pos/L → negative
        assert values[2] < 0  # pos=2.0, shear=-0.2
        # Load right of x=5: shear = Ra = (L-pos)/L → positive
        assert values[8] > 0  # pos=8.0, shear=0.2

    def test_shear_influence_line_at_section(self):
        beam = Beam(length=10.0)
        positions, values = influence_line_shear(beam, 5.0, num_points=11)
        # At load position = 5.0 (index 5), shear = Ra = 0.5
        assert values[5] == pytest.approx(0.5)

    def test_shear_influence_line_invalid_position(self):
        beam = Beam(length=10.0)
        with pytest.raises(ValueError):
            influence_line_shear(beam, 15.0)

    def test_moment_influence_line_zero_at_supports(self):
        beam = Beam(length=10.0)
        positions, values = influence_line_moment(beam, 5.0, num_points=11)
        # Moment at x=5 when load is at support A (pos=0) or B (pos=10) is 0
        assert values[0] == pytest.approx(0.0)
        assert values[-1] == pytest.approx(0.0)


class TestMovingLoadEnvelope:
    """Tests for moving load envelope calculations."""

    def test_single_load_max_moment(self):
        beam = Beam(length=20.0)
        single = LoadTrain(loads=[PointLoad(100.0, 0.0)])
        result = moving_load_envelope(beam, single, num_eval_points=21, num_positions=200)
        # Max moment = P*L/4 = 100*20/4 = 500 at midspan
        mid_idx = 10
        assert result['max_moment'][mid_idx] == pytest.approx(500.0, rel=0.05)

    def test_envelope_reactions_positive(self):
        beam = Beam(length=20.0)
        truck = hl93_design_truck()
        result = moving_load_envelope(beam, truck, num_eval_points=21, num_positions=200)
        assert result['max_reaction_a'] > 0
        assert result['max_reaction_b'] > 0

    def test_symmetric_beam_symmetric_envelope(self):
        beam = Beam(length=20.0)
        single = LoadTrain(loads=[PointLoad(100.0, 0.0)])
        result = moving_load_envelope(beam, single, num_eval_points=21, num_positions=200)
        # Max moment envelope should be roughly symmetric
        assert result['max_moment'][5] == pytest.approx(result['max_moment'][15], rel=0.1)

    def test_envelope_shear_at_supports(self):
        beam = Beam(length=10.0)
        single = LoadTrain(loads=[PointLoad(100.0, 0.0)])
        result = moving_load_envelope(beam, single, num_eval_points=11, num_positions=200)
        # Max shear at left support should be close to the full load
        assert result['max_shear'][0] == pytest.approx(100.0, rel=0.05)


class TestCriticalPositions:
    """Tests for critical load positioning."""

    def test_critical_moment_at_midspan(self):
        beam = Beam(length=20.0)
        single = LoadTrain(loads=[PointLoad(100.0, 0.0)])
        pos, value = critical_positions(beam, single, response="moment", x=10.0)
        # Max moment at midspan = P*L/4 = 500
        assert value == pytest.approx(500.0, rel=0.05)

    def test_critical_reaction_a(self):
        beam = Beam(length=10.0)
        single = LoadTrain(loads=[PointLoad(100.0, 0.0)])
        pos, value = critical_positions(beam, single, response="reaction_a")
        # Max reaction A occurs when load is at support A
        assert value == pytest.approx(100.0, rel=0.05)

    def test_critical_reaction_b(self):
        beam = Beam(length=10.0)
        single = LoadTrain(loads=[PointLoad(100.0, 0.0)])
        pos, value = critical_positions(beam, single, response="reaction_b")
        assert value == pytest.approx(100.0, rel=0.05)

    def test_critical_shear(self):
        beam = Beam(length=10.0)
        single = LoadTrain(loads=[PointLoad(100.0, 0.0)])
        pos, value = critical_positions(beam, single, response="shear", x=5.0)
        assert value > 0

    def test_missing_x_for_moment_raises(self):
        beam = Beam(length=10.0)
        single = LoadTrain(loads=[PointLoad(100.0, 0.0)])
        with pytest.raises(ValueError, match="Position x is required"):
            critical_positions(beam, single, response="moment")

    def test_missing_x_for_shear_raises(self):
        beam = Beam(length=10.0)
        single = LoadTrain(loads=[PointLoad(100.0, 0.0)])
        with pytest.raises(ValueError, match="Position x is required"):
            critical_positions(beam, single, response="shear")

    def test_invalid_response_type_raises(self):
        beam = Beam(length=10.0)
        single = LoadTrain(loads=[PointLoad(100.0, 0.0)])
        with pytest.raises(ValueError, match="Unknown response type"):
            critical_positions(beam, single, response="deflection", x=5.0)


class TestDeflectionEnvelope:
    """Tests for deflection envelope."""

    def test_deflection_zero_at_supports(self):
        beam = Beam(length=10.0)
        single = LoadTrain(loads=[PointLoad(100.0, 0.0)])
        result = compute_deflection_envelope(beam, single, num_eval_points=11, num_positions=200)
        assert result['max_deflection'][0] == pytest.approx(0.0, abs=1e-6)
        assert result['max_deflection'][-1] == pytest.approx(0.0, abs=1e-6)

    def test_max_deflection_at_midspan(self):
        beam = Beam(length=10.0)
        single = LoadTrain(loads=[PointLoad(100.0, 0.0)])
        result = compute_deflection_envelope(beam, single, num_eval_points=11, num_positions=200)
        mid_idx = 5
        # Midspan should have the largest deflection
        assert result['max_deflection'][mid_idx] == max(result['max_deflection'])

    def test_deflection_positive(self):
        beam = Beam(length=10.0)
        single = LoadTrain(loads=[PointLoad(100.0, 0.0)])
        result = compute_deflection_envelope(beam, single, num_eval_points=11, num_positions=200)
        # All deflections should be non-negative
        assert all(d >= -1e-10 for d in result['max_deflection'])

    def test_deflection_known_value(self):
        beam = Beam(length=10.0, E=200e9, I=1e-4)
        single = LoadTrain(loads=[PointLoad(100.0, 0.0)])
        result = compute_deflection_envelope(beam, single, num_eval_points=11, num_positions=500)
        # PL^3/(48*EI) = 100 * 1000 / (48 * 200e9 * 1e-4) = 1.0417e-4
        expected = 100.0 * 10.0**3 / (48 * 200e9 * 1e-4)
        assert result['max_deflection'][5] == pytest.approx(expected, rel=0.05)
