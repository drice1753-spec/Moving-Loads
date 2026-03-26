"""Tests for the Beam module."""

import pytest
from moving_loads.beam import Beam, ContinuousBeam


class TestBeamCreation:
    """Tests for Beam instantiation and validation."""

    def test_create_beam(self):
        beam = Beam(length=10.0)
        assert beam.length == 10.0

    def test_beam_default_properties(self):
        beam = Beam(length=10.0)
        assert beam.E == 200e9
        assert beam.I == 1e-4

    def test_beam_negative_length_raises(self):
        with pytest.raises(ValueError, match="length must be positive"):
            Beam(length=-5.0)

    def test_beam_zero_length_raises(self):
        with pytest.raises(ValueError, match="length must be positive"):
            Beam(length=0.0)


class TestBeamReactions:
    """Tests for simply supported beam reactions."""

    def test_reaction_a_load_at_left(self):
        beam = Beam(length=10.0)
        assert beam.reaction_a(0.0, 100.0) == pytest.approx(100.0)

    def test_reaction_a_load_at_right(self):
        beam = Beam(length=10.0)
        assert beam.reaction_a(10.0, 100.0) == pytest.approx(0.0)

    def test_reaction_a_load_at_midspan(self):
        beam = Beam(length=10.0)
        assert beam.reaction_a(5.0, 100.0) == pytest.approx(50.0)

    def test_reaction_b_load_at_midspan(self):
        beam = Beam(length=10.0)
        assert beam.reaction_b(5.0, 100.0) == pytest.approx(50.0)

    def test_reactions_sum_to_load(self):
        beam = Beam(length=10.0)
        load_pos, load_mag = 3.0, 75.0
        ra = beam.reaction_a(load_pos, load_mag)
        rb = beam.reaction_b(load_pos, load_mag)
        assert ra + rb == pytest.approx(load_mag)


class TestBeamShear:
    """Tests for shear force calculations."""

    def test_shear_left_of_load(self):
        beam = Beam(length=10.0)
        shear = beam.shear(2.0, 5.0, 100.0)
        assert shear == pytest.approx(50.0)

    def test_shear_right_of_load(self):
        beam = Beam(length=10.0)
        shear = beam.shear(7.0, 5.0, 100.0)
        assert shear == pytest.approx(-50.0)


class TestBeamMoment:
    """Tests for bending moment calculations."""

    def test_moment_at_midspan_with_midspan_load(self):
        beam = Beam(length=10.0)
        moment = beam.moment(5.0, 5.0, 100.0)
        # M = Ra * x = 50 * 5 = 250
        assert moment == pytest.approx(250.0)

    def test_moment_at_supports_is_zero(self):
        beam = Beam(length=10.0)
        assert beam.moment(0.0, 5.0, 100.0) == pytest.approx(0.0)
        assert beam.moment(10.0, 5.0, 100.0) == pytest.approx(0.0)


class TestContinuousBeam:
    """Tests for ContinuousBeam."""

    def test_create_continuous_beam(self):
        cb = ContinuousBeam(spans=[10.0, 12.0, 10.0])
        assert cb.num_spans == 3

    def test_total_length(self):
        cb = ContinuousBeam(spans=[10.0, 12.0])
        assert cb.total_length == pytest.approx(22.0)

    def test_empty_spans_raises(self):
        with pytest.raises(ValueError):
            ContinuousBeam(spans=[])

    def test_support_positions(self):
        cb = ContinuousBeam(spans=[10.0, 15.0])
        assert cb.support_positions == pytest.approx([0.0, 10.0, 25.0])
