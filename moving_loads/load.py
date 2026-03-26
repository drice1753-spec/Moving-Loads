"""Load definitions for moving load analysis."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class PointLoad:
    """A single concentrated point load.

    Attributes:
        magnitude: Force magnitude (positive downward).
        offset: Distance from the reference point of the load group.
    """

    magnitude: float
    offset: float = 0.0

    def __post_init__(self):
        if self.magnitude < 0:
            raise ValueError("Load magnitude must be non-negative.")


@dataclass
class DistributedLoad:
    """A uniformly distributed load over a length.

    Attributes:
        intensity: Load per unit length (positive downward).
        length: Length over which the load is applied.
        offset: Distance from reference point to start of distributed load.
    """

    intensity: float
    length: float
    offset: float = 0.0

    def __post_init__(self):
        if self.intensity < 0:
            raise ValueError("Load intensity must be non-negative.")
        if self.length <= 0:
            raise ValueError("Load length must be positive.")

    @property
    def total_force(self) -> float:
        """Total equivalent force."""
        return self.intensity * self.length

    @property
    def centroid_offset(self) -> float:
        """Offset of the load centroid from the reference point."""
        return self.offset + self.length / 2.0

    def to_point_loads(self, num_segments: int = 10) -> list[PointLoad]:
        """Convert to a series of equivalent point loads.

        Args:
            num_segments: Number of segments to divide the distributed load into.

        Returns:
            List of PointLoad objects approximating the distributed load.
        """
        if num_segments <= 0:
            raise ValueError("Number of segments must be positive.")
        segment_length = self.length / num_segments
        segment_force = self.intensity * segment_length
        loads = []
        for i in range(num_segments):
            position = self.offset + segment_length * (i + 0.5)
            loads.append(PointLoad(magnitude=segment_force, offset=position))
        return loads


@dataclass
class LoadTrain:
    """A train of loads that moves across a beam.

    Represents a series of point loads at fixed relative spacing,
    such as axle loads of a vehicle or train.

    Attributes:
        loads: List of PointLoad objects defining the load train.
        name: Optional name for the load configuration.
    """

    loads: list[PointLoad] = field(default_factory=list)
    name: str = ""

    def __post_init__(self):
        if not self.loads:
            raise ValueError("Load train must contain at least one load.")

    @property
    def total_weight(self) -> float:
        """Total weight of all loads in the train."""
        return sum(load.magnitude for load in self.loads)

    @property
    def num_loads(self) -> int:
        """Number of loads in the train."""
        return len(self.loads)

    @property
    def train_length(self) -> float:
        """Total length of the load train (distance from first to last load)."""
        if len(self.loads) <= 1:
            return 0.0
        offsets = [load.offset for load in self.loads]
        return max(offsets) - min(offsets)

    @property
    def centroid(self) -> float:
        """Position of the centroid (resultant) of the load train from the first load."""
        if self.total_weight == 0:
            return 0.0
        return sum(load.magnitude * load.offset for load in self.loads) / self.total_weight

    def positions_on_beam(self, beam_length: float, head_position: float) -> list[tuple[float, float]]:
        """Get absolute positions and magnitudes of loads on a beam.

        Args:
            beam_length: Length of the beam.
            head_position: Position of the reference point (offset=0) on the beam.

        Returns:
            List of (position, magnitude) tuples for loads that are on the beam.
        """
        result = []
        for load in self.loads:
            pos = head_position + load.offset
            if 0 <= pos <= beam_length:
                result.append((pos, load.magnitude))
        return result

    def reversed(self) -> LoadTrain:
        """Return a new LoadTrain with loads in reversed direction.

        Useful for analyzing loads traveling in the opposite direction.
        """
        max_offset = max(load.offset for load in self.loads)
        reversed_loads = [
            PointLoad(magnitude=load.magnitude, offset=max_offset - load.offset)
            for load in self.loads
        ]
        return LoadTrain(loads=reversed_loads, name=f"{self.name} (reversed)")

    def __iter__(self) -> Iterator[PointLoad]:
        return iter(self.loads)

    @classmethod
    def from_axle_spacings(cls, magnitudes: list[float], spacings: list[float], name: str = "") -> LoadTrain:
        """Create a LoadTrain from axle magnitudes and spacings between consecutive axles.

        Args:
            magnitudes: Load magnitudes for each axle.
            spacings: Spacings between consecutive axles (len = len(magnitudes) - 1).
            name: Optional name.

        Returns:
            A new LoadTrain.
        """
        if len(spacings) != len(magnitudes) - 1:
            raise ValueError("Number of spacings must be one less than number of magnitudes.")
        loads = [PointLoad(magnitude=magnitudes[0], offset=0.0)]
        cumulative = 0.0
        for i, spacing in enumerate(spacings):
            cumulative += spacing
            loads.append(PointLoad(magnitude=magnitudes[i + 1], offset=cumulative))
        return cls(loads=loads, name=name)


# Standard load configurations
def hl93_design_truck() -> LoadTrain:
    """AASHTO HL-93 design truck.

    Three axles: 35 kN, 145 kN, 145 kN with spacings of 4.3m and 4.3-9.0m.
    Uses minimum spacing of 4.3m.
    """
    return LoadTrain.from_axle_spacings(
        magnitudes=[35.0, 145.0, 145.0],
        spacings=[4.3, 4.3],
        name="HL-93 Design Truck",
    )


def hl93_design_tandem() -> LoadTrain:
    """AASHTO HL-93 design tandem.

    Two 110 kN axles spaced 1.2m apart.
    """
    return LoadTrain.from_axle_spacings(
        magnitudes=[110.0, 110.0],
        spacings=[1.2],
        name="HL-93 Design Tandem",
    )


def cooper_e80() -> LoadTrain:
    """Cooper E-80 railroad loading.

    Standard railroad bridge design loading.
    """
    magnitudes = [
        80.0, 80.0, 80.0, 80.0,  # 4 driving axles
        80.0, 80.0, 80.0, 80.0,  # 4 driving axles
    ]
    spacings = [1.524, 1.524, 1.524, 2.7432, 1.524, 1.524, 1.524]
    return LoadTrain.from_axle_spacings(
        magnitudes=magnitudes,
        spacings=spacings,
        name="Cooper E-80",
    )
