"""Tests for the Load module."""

import pytest
from moving_loads.load import (
    PointLoad,
    DistributedLoad,
    LoadTrain,
    hl93_design_truck,
    hl93_design_tandem,
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
