# Copyright (c) 2026 UltiMaker
# Cura is released under the terms of the LGPLv3 or higher.

"""Standalone inertial property computation from FDM toolpath segments.

This module computes mass, center of mass, and inertia tensor from arrays
of extrusion segment data and material density. It has **zero** dependencies
on Cura or Uranium -- only numpy and typing are imported.

**Units:**
- Spatial coordinates: millimeters (mm)
- Density: grams per cubic centimeter (g/cm^3)
- Mass: grams (g)
- Inertia tensor: g * mm^2

**Coordinate convention:**
- Y-up (consistent with Cura's scene coordinate system)
- Z may be negated per Cura convention for layer depth

**Algorithm:**
Each extrusion segment is modeled as a thin rectangular-cross-section prism.
Per-segment masses are computed from geometry (width * thickness * length)
and material density, with a /1000 conversion factor for mm^3 * g/cm^3 -> g.
Center of mass is the mass-weighted average of segment midpoints.
The inertia tensor is computed via a two-pass algorithm (CoM first, then
inertia about CoM) with a thin-rod local correction for improved accuracy.
All accumulation uses float64 precision.
"""

import numpy as np
from typing import Tuple

# Line type constants (duplicated from LayerPolygon to avoid import dependency)
# Inset0Type=1, InsetXType=2, SkinType=3, InfillType=6
PART_LINE_TYPES = frozenset({1, 2, 3, 6})
_PART_TYPE_LIST = sorted(PART_LINE_TYPES)  # pre-sorted list for np.isin


def filter_part_segments(types: np.ndarray) -> np.ndarray:
    """Return a boolean mask selecting only part-material segments.

    :param types: 1D array of uint8 line types (from LayerPolygon.types)
    :return: boolean mask of shape (N,) where True indicates a part segment
    """
    return np.isin(types.ravel(), _PART_TYPE_LIST)


def compute_inertial_properties(
    starts: np.ndarray,      # (N, 3) float64 - segment start points in mm
    ends: np.ndarray,        # (N, 3) float64 - segment end points in mm
    widths: np.ndarray,      # (N,) float64 - line widths in mm
    thicknesses: np.ndarray, # (N,) float64 - line thicknesses in mm
    density: float,          # g/cm^3
) -> Tuple[float, np.ndarray, np.ndarray]:
    """Compute mass, center of mass, and inertia tensor from segment data.

    Models each extrusion segment as a thin rectangular-cross-section prism
    and computes aggregate inertial properties using vectorized numpy operations.
    The inertia tensor is computed directly about the center of mass using a
    two-pass algorithm for numerical stability, with a thin-rod local correction.

    :param starts: segment start points, shape (N, 3), units mm, dtype float64
    :param ends: segment end points, shape (N, 3), units mm, dtype float64
    :param widths: per-segment line widths, shape (N,), units mm, dtype float64
    :param thicknesses: per-segment line thicknesses, shape (N,), units mm, dtype float64
    :param density: material density in g/cm^3
    :return: tuple of (mass_grams, center_of_mass_mm, inertia_tensor_g_mm2)
             where mass is a float, center_of_mass is shape (3,), tensor is shape (3, 3)
    """
    # Empty input guard
    if len(starts) == 0:
        return 0.0, np.zeros(3, dtype=np.float64), np.zeros((3, 3), dtype=np.float64)

    # Segment geometry
    d = ends - starts                              # (N, 3)
    lengths = np.linalg.norm(d, axis=1)            # (N,)

    # Filter zero-length segments (RBST-02)
    valid = lengths > 1e-6
    if not np.all(valid):
        starts = starts[valid]
        ends = ends[valid]
        d = d[valid]
        lengths = lengths[valid]
        widths = widths[valid]
        thicknesses = thicknesses[valid]

    # Check if any valid segments remain
    if len(starts) == 0:
        return 0.0, np.zeros(3, dtype=np.float64), np.zeros((3, 3), dtype=np.float64)

    # Mass computation (COMP-01)
    # volume_mm3 * density_g_per_cm3 / 1000 = mass_grams
    # (1 cm^3 = 1000 mm^3, so dividing by 1000 converts mm^3 * g/cm^3 to grams)
    volumes = widths * thicknesses * lengths       # (N,) mm^3
    masses = volumes * density / 1000.0            # (N,) grams
    total_mass = np.sum(masses)

    if total_mass < 1e-15:
        return 0.0, np.zeros(3, dtype=np.float64), np.zeros((3, 3), dtype=np.float64)

    # Center of mass (COMP-03)
    midpoints = (starts + ends) / 2.0              # (N, 3)
    com = np.sum(masses[:, np.newaxis] * midpoints, axis=0) / total_mass  # (3,)

    # Inertia tensor about CoM (COMP-04) -- two-pass for numerical stability
    r = midpoints - com                            # (N, 3) vectors from CoM to midpoints
    r_sq = np.sum(r * r, axis=1)                   # (N,) squared distances

    # Parallel axis contribution: sum_i m_i * (|r_i|^2 * I_3 - r_i outer r_i)
    I = np.sum(masses * r_sq) * np.eye(3, dtype=np.float64)
    wr = np.sqrt(masses)[:, np.newaxis] * r        # (N, 3)
    I -= wr.T @ wr                                 # V^T @ V trick for outer product sum

    # Thin-rod local inertia correction
    safe_lengths = np.maximum(lengths, 1e-15)
    unit_d = d / safe_lengths[:, np.newaxis]       # (N, 3) unit directions
    rod_factor = masses * lengths ** 2 / 12.0      # (N,)
    I += np.sum(rod_factor) * np.eye(3, dtype=np.float64)
    wu = np.sqrt(rod_factor)[:, np.newaxis] * unit_d
    I -= wu.T @ wu

    # Enforce exact symmetry (floating point cleanup)
    I = (I + I.T) / 2.0

    return total_mass, com, I
