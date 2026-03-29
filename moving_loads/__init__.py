"""Moving Loads Analysis Library.

A structural engineering library for analyzing moving loads on beams.
Computes influence lines, maximum reactions, shear, and bending moment
envelopes for simply supported and continuous beams.
"""

from moving_loads.beam import Beam, ContinuousBeam
from moving_loads.load import PointLoad, DistributedLoad, LoadTrain
from moving_loads.analysis import (
    influence_line_reaction,
    influence_line_shear,
    influence_line_moment,
    moving_load_envelope,
    critical_positions,
)
from moving_loads.freight_calculator import (
    FreightResult,
    calculate_freight,
    get_road_distance,
)

__version__ = "0.1.0"

__all__ = [
    "Beam",
    "ContinuousBeam",
    "PointLoad",
    "DistributedLoad",
    "LoadTrain",
    "influence_line_reaction",
    "influence_line_shear",
    "influence_line_moment",
    "moving_load_envelope",
    "critical_positions",
    "FreightResult",
    "calculate_freight",
    "get_road_distance",
]
