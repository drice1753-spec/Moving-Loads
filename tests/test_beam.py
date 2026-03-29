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


class TestBeamValidation:
    """Tests for Beam parameter validation."""

    def test_negative_E_raises(self):
        with pytest.raises(ValueError, match="Modulus of elasticity must be positive"):
            Beam(length=10.0, E=-1.0)

    def test_zero_E_raises(self):
        with pytest.raises(ValueError, match="Modulus of elasticity must be positive"):
            Beam(length=10.0, E=0.0)

    def test_negative_I_raises(self):
        with pytest.raises(ValueError, match="Second moment of area must be positive"):
            Beam(length=10.0, I=-1e-4)

    def test_zero_I_raises(self):
        with pytest.raises(ValueError, match="Second moment of area must be positive"):
            Beam(length=10.0, I=0.0)

    def test_stiffness_property(self):
        beam = Beam(length=10.0, E=200e9, I=1e-4)
        assert beam.stiffness == pytest.approx(200e9 * 1e-4)

    def test_max_moment_position(self):
        beam = Beam(length=10.0)
        assert beam.max_moment_position() == pytest.approx(5.0)

    def test_reaction_out_of_range_raises(self):
        beam = Beam(length=10.0)
        with pytest.raises(ValueError, match="within beam span"):
            beam.reaction_a(-1.0, 100.0)
        with pytest.raises(ValueError, match="within beam span"):
            beam.reaction_b(11.0, 100.0)

    def test_shear_out_of_range_raises(self):
        beam = Beam(length=10.0)
        with pytest.raises(ValueError, match="within beam span"):
            beam.shear(-1.0, 5.0, 100.0)

    def test_moment_out_of_range_raises(self):
        beam = Beam(length=10.0)
        with pytest.raises(ValueError, match="within beam span"):
            beam.moment(11.0, 5.0, 100.0)


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


class TestBeamDeflection:
    """Tests for Beam deflection calculations."""

    def test_deflection_at_midspan_known_formula(self):
        beam = Beam(length=10.0, E=200e9, I=1e-4)
        # PL^3 / (48EI) for midspan load at midspan
        expected = 100.0 * 10.0**3 / (48 * 200e9 * 1e-4)
        assert beam.deflection(5.0, 5.0, 100.0) == pytest.approx(expected, rel=1e-6)

    def test_deflection_zero_at_supports(self):
        beam = Beam(length=10.0)
        assert beam.deflection(0.0, 5.0, 100.0) == pytest.approx(0.0, abs=1e-10)
        assert beam.deflection(10.0, 5.0, 100.0) == pytest.approx(0.0, abs=1e-10)

    def test_deflection_asymmetric_load(self):
        beam = Beam(length=10.0)
        # Load at quarter point — deflection should be positive (downward) at midspan
        d = beam.deflection(5.0, 2.5, 100.0)
        assert d > 0

    def test_deflection_out_of_range_raises(self):
        beam = Beam(length=10.0)
        with pytest.raises(ValueError, match="within beam span"):
            beam.deflection(-1.0, 5.0, 100.0)


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

    def test_negative_span_raises(self):
        with pytest.raises(ValueError, match="positive"):
            ContinuousBeam(spans=[10.0, -5.0])

    def test_support_positions(self):
        cb = ContinuousBeam(spans=[10.0, 15.0])
        assert cb.support_positions == pytest.approx([0.0, 10.0, 25.0])

    def test_span_index_first_span(self):
        cb = ContinuousBeam(spans=[10.0, 15.0, 10.0])
        assert cb.span_index(5.0) == 0

    def test_span_index_second_span(self):
        cb = ContinuousBeam(spans=[10.0, 15.0, 10.0])
        assert cb.span_index(15.0) == 1

    def test_span_index_last_span(self):
        cb = ContinuousBeam(spans=[10.0, 15.0, 10.0])
        assert cb.span_index(30.0) == 2

    def test_span_index_at_boundary(self):
        cb = ContinuousBeam(spans=[10.0, 10.0])
        assert cb.span_index(10.0) == 0  # On boundary, belongs to first span

    def test_span_index_out_of_range_raises(self):
        cb = ContinuousBeam(spans=[10.0, 10.0])
        with pytest.raises(ValueError, match="outside the beam"):
            cb.span_index(-1.0)
        with pytest.raises(ValueError, match="outside the beam"):
            cb.span_index(25.0)

    def test_three_moment_equation_returns_correct_count(self):
        cb = ContinuousBeam(spans=[10.0, 12.0, 10.0])
        moments = cb.three_moment_equation()
        assert len(moments) == 4  # num_spans + 1
