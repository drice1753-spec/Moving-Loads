"""Beam models for structural analysis."""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Beam:
    """Simply supported beam with two supports at the ends.

    Attributes:
        length: Span length of the beam (must be positive).
        E: Modulus of elasticity (default 200e9 Pa for steel).
        I: Second moment of area (default 1e-4 m^4).
    """

    length: float
    E: float = 200e9
    I: float = 1e-4

    def __post_init__(self):
        if self.length <= 0:
            raise ValueError("Beam length must be positive.")
        if self.E <= 0:
            raise ValueError("Modulus of elasticity must be positive.")
        if self.I <= 0:
            raise ValueError("Second moment of area must be positive.")

    @property
    def stiffness(self) -> float:
        """Flexural stiffness EI."""
        return self.E * self.I

    def reaction_a(self, load_position: float, load_magnitude: float) -> float:
        """Reaction at support A (left) for a point load.

        Args:
            load_position: Distance from support A.
            load_magnitude: Magnitude of the point load.

        Returns:
            Reaction force at support A.
        """
        if not 0 <= load_position <= self.length:
            raise ValueError("Load position must be within beam span.")
        return load_magnitude * (self.length - load_position) / self.length

    def reaction_b(self, load_position: float, load_magnitude: float) -> float:
        """Reaction at support B (right) for a point load."""
        if not 0 <= load_position <= self.length:
            raise ValueError("Load position must be within beam span.")
        return load_magnitude * load_position / self.length

    def shear(self, x: float, load_position: float, load_magnitude: float) -> float:
        """Shear force at position x due to a point load.

        Args:
            x: Position along the beam to evaluate shear.
            load_position: Position of the point load from support A.
            load_magnitude: Magnitude of the point load.

        Returns:
            Shear force at position x.
        """
        if not 0 <= x <= self.length:
            raise ValueError("Position x must be within beam span.")
        ra = self.reaction_a(load_position, load_magnitude)
        if x <= load_position:
            return ra
        else:
            return ra - load_magnitude

    def moment(self, x: float, load_position: float, load_magnitude: float) -> float:
        """Bending moment at position x due to a point load.

        Args:
            x: Position along the beam to evaluate moment.
            load_position: Position of the point load from support A.
            load_magnitude: Magnitude of the point load.

        Returns:
            Bending moment at position x.
        """
        if not 0 <= x <= self.length:
            raise ValueError("Position x must be within beam span.")
        ra = self.reaction_a(load_position, load_magnitude)
        if x <= load_position:
            return ra * x
        else:
            return ra * x - load_magnitude * (x - load_position)

    def max_moment_position(self) -> float:
        """Position of maximum moment for a single load (midspan)."""
        return self.length / 2.0

    def deflection(self, x: float, load_position: float, load_magnitude: float) -> float:
        """Deflection at position x due to a point load using Euler-Bernoulli beam theory.

        Args:
            x: Position along the beam.
            load_position: Position of the point load.
            load_magnitude: Magnitude of the point load.

        Returns:
            Deflection at position x (positive downward).
        """
        if not 0 <= x <= self.length:
            raise ValueError("Position x must be within beam span.")
        L = self.length
        a = load_position
        b = L - a
        EI = self.stiffness

        if x <= a:
            return (load_magnitude * b * x) / (6 * L * EI) * (L**2 - b**2 - x**2)
        else:
            return (load_magnitude * a * (L - x)) / (6 * L * EI) * (2 * L * x - a**2 - x**2)


@dataclass
class ContinuousBeam:
    """Multi-span continuous beam.

    Attributes:
        spans: List of span lengths.
        E: Modulus of elasticity.
        I: Second moment of area (assumed constant).
    """

    spans: list[float] = field(default_factory=list)
    E: float = 200e9
    I: float = 1e-4

    def __post_init__(self):
        if not self.spans:
            raise ValueError("At least one span must be provided.")
        if any(s <= 0 for s in self.spans):
            raise ValueError("All span lengths must be positive.")

    @property
    def total_length(self) -> float:
        """Total length of the continuous beam."""
        return sum(self.spans)

    @property
    def num_spans(self) -> int:
        """Number of spans."""
        return len(self.spans)

    @property
    def support_positions(self) -> list[float]:
        """Positions of all supports from the left end."""
        positions = [0.0]
        cumulative = 0.0
        for span in self.spans:
            cumulative += span
            positions.append(cumulative)
        return positions

    def span_index(self, x: float) -> int:
        """Determine which span a given position falls in.

        Args:
            x: Position from the left end.

        Returns:
            Zero-based span index.

        Raises:
            ValueError: If x is outside the beam.
        """
        if x < 0 or x > self.total_length:
            raise ValueError(f"Position {x} is outside the beam (0 to {self.total_length}).")
        cumulative = 0.0
        for i, span in enumerate(self.spans):
            cumulative += span
            if x <= cumulative:
                return i
        return self.num_spans - 1

    def three_moment_equation(self, moments_at_supports: list[float] | None = None) -> list[float]:
        """Solve for support moments using the three-moment equation.

        This is a simplified implementation for uniform EI and no settlement.
        Returns moments at interior supports given applied loading.

        For now, returns zero moments (placeholder for full implementation).
        """
        return [0.0] * (self.num_spans + 1)
