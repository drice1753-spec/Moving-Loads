"""Moving load analysis functions.

Provides influence line computation, envelope analysis, and critical
load positioning for beams under moving loads.
"""

from __future__ import annotations
import numpy as np
from moving_loads.beam import Beam, ContinuousBeam
from moving_loads.load import LoadTrain, DistributedLoad


def influence_line_reaction(beam: Beam, support: str, num_points: int = 100) -> tuple[np.ndarray, np.ndarray]:
    """Compute the influence line for a support reaction.

    Args:
        beam: The beam to analyze.
        support: 'A' for left support, 'B' for right support.
        num_points: Number of evaluation points.

    Returns:
        Tuple of (positions, influence_values) arrays.
    """
    if support not in ('A', 'B'):
        raise ValueError("Support must be 'A' or 'B'.")
    positions = np.linspace(0, beam.length, num_points)
    if support == 'A':
        values = (beam.length - positions) / beam.length
    else:
        values = positions / beam.length
    return positions, values


def influence_line_shear(beam: Beam, x: float, num_points: int = 100) -> tuple[np.ndarray, np.ndarray]:
    """Compute the influence line for shear at position x.

    Args:
        beam: The beam to analyze.
        x: Position where shear is evaluated.
        num_points: Number of evaluation points.

    Returns:
        Tuple of (load_positions, influence_values) arrays.
    """
    if not 0 <= x <= beam.length:
        raise ValueError("Position x must be within beam span.")
    positions = np.linspace(0, beam.length, num_points)
    values = np.zeros(num_points)
    for i, pos in enumerate(positions):
        values[i] = beam.shear(x, pos, 1.0)
    return positions, values


def influence_line_moment(beam: Beam, x: float, num_points: int = 100) -> tuple[np.ndarray, np.ndarray]:
    """Compute the influence line for bending moment at position x.

    Args:
        beam: The beam to analyze.
        x: Position where moment is evaluated.
        num_points: Number of evaluation points.

    Returns:
        Tuple of (load_positions, influence_values) arrays.
    """
    if not 0 <= x <= beam.length:
        raise ValueError("Position x must be within beam span.")
    positions = np.linspace(0, beam.length, num_points)
    values = np.zeros(num_points)
    for i, pos in enumerate(positions):
        values[i] = beam.moment(x, pos, 1.0)
    return positions, values


def moving_load_envelope(
    beam: Beam,
    load_train: LoadTrain,
    num_eval_points: int = 50,
    num_positions: int = 200,
) -> dict[str, np.ndarray]:
    """Compute the envelope of maximum/minimum responses as a load train crosses a beam.

    Args:
        beam: The beam to analyze.
        load_train: The moving load train.
        num_eval_points: Number of points along beam to evaluate responses.
        num_positions: Number of load train positions to consider.

    Returns:
        Dictionary with keys:
            'x': evaluation points along the beam
            'max_shear': maximum shear at each point
            'min_shear': minimum shear at each point
            'max_moment': maximum moment at each point
            'min_moment': minimum moment at each point
            'max_reaction_a': maximum reaction at support A
            'max_reaction_b': maximum reaction at support B
    """
    eval_points = np.linspace(0, beam.length, num_eval_points)

    max_shear = np.full(num_eval_points, -np.inf)
    min_shear = np.full(num_eval_points, np.inf)
    max_moment = np.full(num_eval_points, -np.inf)
    min_moment = np.full(num_eval_points, np.inf)
    max_reaction_a = 0.0
    max_reaction_b = 0.0

    # Move the load train across the beam
    start = -load_train.train_length
    end = beam.length
    head_positions = np.linspace(start, end, num_positions)

    for head_pos in head_positions:
        loads_on_beam = load_train.positions_on_beam(beam.length, head_pos)
        if not loads_on_beam:
            continue

        # Compute reactions
        ra = sum(beam.reaction_a(pos, mag) for pos, mag in loads_on_beam)
        rb = sum(beam.reaction_b(pos, mag) for pos, mag in loads_on_beam)
        max_reaction_a = max(max_reaction_a, ra)
        max_reaction_b = max(max_reaction_b, rb)

        # Compute shear and moment at each evaluation point
        for j, x in enumerate(eval_points):
            shear = sum(beam.shear(x, pos, mag) for pos, mag in loads_on_beam)
            moment = sum(beam.moment(x, pos, mag) for pos, mag in loads_on_beam)

            max_shear[j] = max(max_shear[j], shear)
            min_shear[j] = min(min_shear[j], shear)
            max_moment[j] = max(max_moment[j], moment)
            min_moment[j] = min(min_moment[j], moment)

    return {
        'x': eval_points,
        'max_shear': max_shear,
        'min_shear': min_shear,
        'max_moment': max_moment,
        'min_moment': min_moment,
        'max_reaction_a': max_reaction_a,
        'max_reaction_b': max_reaction_b,
    }


def critical_positions(
    beam: Beam,
    load_train: LoadTrain,
    response: str = "moment",
    x: float | None = None,
    num_positions: int = 500,
) -> tuple[float, float]:
    """Find the critical position of a load train that maximizes a response.

    Args:
        beam: The beam to analyze.
        load_train: The moving load train.
        response: Type of response ('moment', 'shear', 'reaction_a', 'reaction_b').
        x: Position on beam to evaluate (required for 'moment' and 'shear').
        num_positions: Number of positions to search.

    Returns:
        Tuple of (critical_head_position, max_response_value).
    """
    if response in ('moment', 'shear') and x is None:
        raise ValueError(f"Position x is required for {response} analysis.")

    start = -load_train.train_length
    end = beam.length
    head_positions = np.linspace(start, end, num_positions)

    max_value = -np.inf
    critical_pos = 0.0

    for head_pos in head_positions:
        loads_on_beam = load_train.positions_on_beam(beam.length, head_pos)
        if not loads_on_beam:
            continue

        if response == 'moment':
            value = sum(beam.moment(x, pos, mag) for pos, mag in loads_on_beam)
        elif response == 'shear':
            value = sum(beam.shear(x, pos, mag) for pos, mag in loads_on_beam)
        elif response == 'reaction_a':
            value = sum(beam.reaction_a(pos, mag) for pos, mag in loads_on_beam)
        elif response == 'reaction_b':
            value = sum(beam.reaction_b(pos, mag) for pos, mag in loads_on_beam)
        else:
            raise ValueError(f"Unknown response type: {response}")

        if value > max_value:
            max_value = value
            critical_pos = head_pos

    return critical_pos, max_value


def compute_deflection_envelope(
    beam: Beam,
    load_train: LoadTrain,
    num_eval_points: int = 50,
    num_positions: int = 200,
) -> dict[str, np.ndarray]:
    """Compute the deflection envelope as a load train crosses a beam.

    Args:
        beam: The beam to analyze.
        load_train: The moving load train.
        num_eval_points: Number of evaluation points.
        num_positions: Number of load train positions.

    Returns:
        Dictionary with 'x' and 'max_deflection' arrays.
    """
    eval_points = np.linspace(0, beam.length, num_eval_points)
    max_deflection = np.zeros(num_eval_points)

    start = -load_train.train_length
    end = beam.length
    head_positions = np.linspace(start, end, num_positions)

    for head_pos in head_positions:
        loads_on_beam = load_train.positions_on_beam(beam.length, head_pos)
        if not loads_on_beam:
            continue

        for j, x in enumerate(eval_points):
            deflection = sum(
                beam.deflection(x, pos, mag) for pos, mag in loads_on_beam
            )
            max_deflection[j] = max(max_deflection[j], deflection)

    return {
        'x': eval_points,
        'max_deflection': max_deflection,
    }
