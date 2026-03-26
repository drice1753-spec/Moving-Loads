# Test Coverage Analysis

## Current State

| Module | Statements | Missed | Coverage |
|---|---|---|---|
| `moving_loads/__init__.py` | 5 | 0 | **100%** |
| `moving_loads/analysis.py` | 94 | 46 | **51%** |
| `moving_loads/beam.py` | 86 | 27 | **69%** |
| `moving_loads/load.py` | 91 | 14 | **85%** |
| `moving_loads/utils.py` | 34 | 34 | **0%** |
| **TOTAL** | **310** | **121** | **61%** |

**36 tests passing, 0 failures.**

---

## Coverage Gaps & Recommendations

### 1. `utils.py` — 0% Coverage (HIGH PRIORITY)

The entire utilities module is untested. This includes:

- **`interpolate_linear()`** — Linear interpolation between data points. Needs tests for: normal interpolation, boundary values, mismatched array lengths, insufficient data points, out-of-range queries.
- **`compute_resultant()`** — Resultant force calculation. Needs tests for: single force, multiple forces, empty list, zero-magnitude forces.
- **`find_max_index()` / `find_min_index()`** — Array index finders. Needs tests for: normal arrays, arrays with ties, single-element arrays.
- **`format_results_table()`** — Output formatting. Needs tests to verify correct column alignment and value formatting.

### 2. `analysis.py` — 51% Coverage (HIGH PRIORITY)

Several key analysis functions have no tests at all:

- **`influence_line_shear()`** (lines 45-51) — No tests exist. Needs tests verifying the characteristic shape of shear influence lines (positive before the evaluation point, negative after).
- **`critical_positions()`** (lines 163-193) — Entirely untested. This is a core function for engineering use. Needs tests for:
  - Critical position for maximum moment at midspan
  - Critical position for maximum shear at supports
  - Critical position for maximum reactions
  - Error handling when `x` is not provided for moment/shear
  - Invalid response type
- **`compute_deflection_envelope()`** (lines 213-231) — Entirely untested. Needs tests verifying:
  - Maximum deflection occurs at midspan for a single load
  - Deflection is zero at supports
  - Deflection values are physically reasonable (positive downward)
- **Edge cases in `moving_load_envelope()`** — Only basic happy-path tested. Needs:
  - Symmetric beam/load produces symmetric envelope
  - Envelope shear values at supports
  - Multi-axle load train produces larger responses than single loads

### 3. `beam.py` — 69% Coverage (MEDIUM PRIORITY)

Missing coverage areas:

- **`Beam` validation edge cases** (lines 25, 27) — No tests for invalid `E` or `I` values (negative/zero modulus of elasticity or moment of inertia).
- **`Beam.stiffness` property** (line 32) — Untested. Simple but should verify `E * I`.
- **`Beam.max_moment_position()`** (line 94) — Untested. Should verify it returns `L/2`.
- **`Beam.deflection()`** (lines 107-117) — Entirely untested. Critical for serviceability checks. Needs:
  - Deflection at midspan for midspan load (known formula: `PL³/48EI`)
  - Deflection is zero at supports
  - Deflection for asymmetric load position
- **`Beam` out-of-range errors** (lines 45, 51, 66, 85) — Boundary validation tests for `reaction_a`, `reaction_b`, `shear`, `moment` when load or evaluation position is outside the beam.
- **`ContinuousBeam.span_index()`** (lines 138, 172-179) — Untested. Needs tests for positions in different spans, at boundaries, and outside the beam.
- **`ContinuousBeam.three_moment_equation()`** (line 189) — Untested placeholder. At minimum, verify it returns the correct number of zero values.

### 4. `load.py` — 85% Coverage (MEDIUM PRIORITY)

Remaining gaps:

- **`DistributedLoad` validation** (lines 41, 43) — No tests for negative intensity or non-positive length.
- **`DistributedLoad.to_point_loads()` with invalid segments** (line 65) — No test for `num_segments <= 0`.
- **`DistributedLoad.to_point_loads()` force equivalence** — Should verify that the sum of point load magnitudes equals `total_force`.
- **`LoadTrain.centroid`** (lines 115-117) — Untested. Should verify centroid position for symmetric and asymmetric load trains.
- **`LoadTrain.positions_on_beam()`** — Untested directly. Needs tests for:
  - All loads on beam
  - Some loads off beam (partially on)
  - All loads off beam
- **`LoadTrain.reversed()`** (lines 141-146) — Untested. Should verify loads are mirrored correctly and total weight is preserved.
- **`LoadTrain.__iter__()`** (line 149) — Untested. Verify iteration yields all loads.
- **`LoadTrain.from_axle_spacings()` error handling** (line 164) — No test for mismatched magnitudes/spacings lengths.
- **`cooper_e80()`** (lines 204-209) — Standard load config untested.

---

## Recommended Testing Priorities

### Phase 1 — Critical gaps (bring coverage to ~85%)
1. Add `tests/test_utils.py` covering all utility functions
2. Add tests for `influence_line_shear()`, `critical_positions()`, and `compute_deflection_envelope()`
3. Add tests for `Beam.deflection()` with known analytical solutions

### Phase 2 — Validation & edge cases (bring coverage to ~95%)
4. Add boundary/error tests for `Beam` methods (invalid positions, invalid E/I)
5. Add tests for `ContinuousBeam.span_index()` and `three_moment_equation()`
6. Add tests for `LoadTrain.reversed()`, `centroid`, `positions_on_beam()`, and `__iter__()`
7. Add `DistributedLoad` validation and force-equivalence tests

### Phase 3 — Engineering validation
8. Add integration tests comparing results against known analytical solutions (e.g., AISC beam formulas)
9. Add tests for standard load configurations (`cooper_e80`)
10. Add property-based tests (e.g., equilibrium: sum of reactions equals total load for any position)
