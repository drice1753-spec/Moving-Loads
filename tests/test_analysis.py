"""Tests for the analysis module."""

import numpy as np
import pytest
from moving_loads.beam import Beam
from moving_loads.load import PointLoad, LoadTrain, hl93_design_truck
from moving_loads.analysis import (
    influence_line_reaction,
    influence_line_moment,
    moving_load_envelope,
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
