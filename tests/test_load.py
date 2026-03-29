"""Tests for the Load module."""

import pytest
from moving_loads.load import (
    PointLoad,
    DistributedLoad,
    LoadTrain,
    hl93_design_truck,
    hl93_design_tandem,
    cooper_e80,
)


class TestPointLoad:
    """Tests for PointLoad."""

    def test_create_point_load(self):
        pl = PointLoad(magnitude=100.0, offset=5.0)
        assert pl.magnitude == 100.0
        assert pl.offset == 5.0

    def test_default_offset(self):
        pl = PointLoad(magnitude=50.0)
        assert pl.offset == 0.0

    def test_negative_magnitude_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            PointLoad(magnitude=-10.0)


class TestDistributedLoad:
    """Tests for DistributedLoad."""

    def test_total_force(self):
        dl = DistributedLoad(intensity=10.0, length=5.0)
        assert dl.total_force == pytest.approx(50.0)

    def test_centroid_offset(self):
        dl = DistributedLoad(intensity=10.0, length=4.0, offset=2.0)
        assert dl.centroid_offset == pytest.approx(4.0)

    def test_to_point_loads_count(self):
        dl = DistributedLoad(intensity=10.0, length=5.0)
        points = dl.to_point_loads(num_segments=5)
        assert len(points) == 5

    def test_to_point_loads_force_equivalence(self):
        dl = DistributedLoad(intensity=10.0, length=5.0)
        points = dl.to_point_loads(num_segments=10)
        total = sum(p.magnitude for p in points)
        assert total == pytest.approx(dl.total_force)

    def test_negative_intensity_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            DistributedLoad(intensity=-5.0, length=3.0)

    def test_non_positive_length_raises(self):
        with pytest.raises(ValueError, match="length must be positive"):
            DistributedLoad(intensity=10.0, length=0.0)

    def test_to_point_loads_zero_segments_raises(self):
        dl = DistributedLoad(intensity=10.0, length=5.0)
        with pytest.raises(ValueError, match="positive"):
            dl.to_point_loads(num_segments=0)


class TestLoadTrain:
    """Tests for LoadTrain."""

    def test_create_load_train(self):
        loads = [PointLoad(100.0, 0.0), PointLoad(100.0, 4.0)]
        lt = LoadTrain(loads=loads)
        assert lt.num_loads == 2

    def test_total_weight(self):
        loads = [PointLoad(100.0, 0.0), PointLoad(200.0, 4.0)]
        lt = LoadTrain(loads=loads)
        assert lt.total_weight == pytest.approx(300.0)

    def test_train_length(self):
        loads = [PointLoad(100.0, 0.0), PointLoad(100.0, 8.6)]
        lt = LoadTrain(loads=loads)
        assert lt.train_length == pytest.approx(8.6)

    def test_from_axle_spacings(self):
        lt = LoadTrain.from_axle_spacings(
            magnitudes=[35.0, 145.0, 145.0],
            spacings=[4.3, 4.3],
        )
        assert lt.num_loads == 3
        assert lt.loads[2].offset == pytest.approx(8.6)

    def test_empty_load_train_raises(self):
        with pytest.raises(ValueError):
            LoadTrain(loads=[])

    def test_centroid_symmetric(self):
        loads = [PointLoad(100.0, 0.0), PointLoad(100.0, 10.0)]
        lt = LoadTrain(loads=loads)
        assert lt.centroid == pytest.approx(5.0)

    def test_centroid_asymmetric(self):
        loads = [PointLoad(100.0, 0.0), PointLoad(300.0, 10.0)]
        lt = LoadTrain(loads=loads)
        assert lt.centroid == pytest.approx(7.5)

    def test_train_length_single_load(self):
        lt = LoadTrain(loads=[PointLoad(100.0, 0.0)])
        assert lt.train_length == pytest.approx(0.0)

    def test_positions_on_beam_all_on(self):
        loads = [PointLoad(100.0, 0.0), PointLoad(100.0, 4.0)]
        lt = LoadTrain(loads=loads)
        result = lt.positions_on_beam(beam_length=20.0, head_position=5.0)
        assert len(result) == 2
        assert result[0] == (5.0, 100.0)
        assert result[1] == (9.0, 100.0)

    def test_positions_on_beam_partially_off(self):
        loads = [PointLoad(100.0, 0.0), PointLoad(100.0, 4.0)]
        lt = LoadTrain(loads=loads)
        # Head at -2: first load at -2 (off), second at 2 (on)
        result = lt.positions_on_beam(beam_length=10.0, head_position=-2.0)
        assert len(result) == 1
        assert result[0][0] == pytest.approx(2.0)

    def test_positions_on_beam_all_off(self):
        loads = [PointLoad(100.0, 0.0), PointLoad(100.0, 4.0)]
        lt = LoadTrain(loads=loads)
        result = lt.positions_on_beam(beam_length=10.0, head_position=-10.0)
        assert len(result) == 0

    def test_reversed_preserves_weight(self):
        lt = LoadTrain.from_axle_spacings(
            magnitudes=[35.0, 145.0, 145.0],
            spacings=[4.3, 4.3],
        )
        rev = lt.reversed()
        assert rev.total_weight == pytest.approx(lt.total_weight)

    def test_reversed_mirrors_offsets(self):
        loads = [PointLoad(100.0, 0.0), PointLoad(200.0, 10.0)]
        lt = LoadTrain(loads=loads)
        rev = lt.reversed()
        assert rev.loads[0].offset == pytest.approx(10.0)
        assert rev.loads[1].offset == pytest.approx(0.0)
        assert rev.loads[0].magnitude == pytest.approx(100.0)
        assert rev.loads[1].magnitude == pytest.approx(200.0)

    def test_iter(self):
        loads = [PointLoad(100.0, 0.0), PointLoad(200.0, 4.0)]
        lt = LoadTrain(loads=loads)
        iterated = list(lt)
        assert len(iterated) == 2
        assert iterated[0].magnitude == 100.0

    def test_from_axle_spacings_mismatched_raises(self):
        with pytest.raises(ValueError, match="one less"):
            LoadTrain.from_axle_spacings(
                magnitudes=[100.0, 200.0],
                spacings=[4.0, 5.0],
            )


class TestStandardLoads:
    """Tests for standard load configurations."""

    def test_hl93_truck(self):
        truck = hl93_design_truck()
        assert truck.num_loads == 3
        assert truck.total_weight == pytest.approx(325.0)

    def test_hl93_tandem(self):
        tandem = hl93_design_tandem()
        assert tandem.num_loads == 2
        assert tandem.total_weight == pytest.approx(220.0)

    def test_cooper_e80(self):
        e80 = cooper_e80()
        assert e80.num_loads == 8
        assert e80.total_weight == pytest.approx(640.0)
