# Copyright (c) 2026 UltiMaker
# Cura is released under the terms of the LGPLv3 or higher.

"""Background job that computes inertial properties from raw protobuf layer data.

This job parses LayerOptimized protobuf messages (the same messages stored by
CuraEngineBackend in ``_stored_optimized_layer_data``), extracts segment geometry,
applies coordinate transforms, and delegates to the Phase 1 computation engine.

It runs on a background thread via :class:`UM.Job.Job` and stores results as
instance attributes that are read from the ``finished`` callback on the main thread.
"""

import numpy
from typing import List, Optional

from UM.Job import Job
from UM.Application import Application
from UM.Logger import Logger

from cura.InertialComputation import compute_inertial_properties, filter_part_segments

DEFAULT_DENSITY = 1.24  # PLA density in g/cm^3


class ComputeInertialPropertiesJob(Job):
    """Parse protobuf layers and compute mass, center of mass, and inertia tensor."""

    def __init__(
        self,
        layers: List,
        density: Optional[float] = None,
        machine_center_is_zero: Optional[bool] = None,
        machine_width: Optional[float] = None,
        machine_depth: Optional[float] = None,
    ) -> None:
        """
        :param layers: List of LayerOptimized Arcus.PythonMessage objects (captured reference).
        :param density: Material density in g/cm^3.  If None, read from Application at runtime.
        :param machine_center_is_zero: Build plate origin setting.  If None, read from Application.
        :param machine_width: Build plate width in mm.  If None, read from Application.
        :param machine_depth: Build plate depth in mm.  If None, read from Application.
        """
        super().__init__()
        self._layers = layers
        self._density: Optional[float] = density
        self._machine_center_is_zero: Optional[bool] = machine_center_is_zero
        self._machine_width: Optional[float] = machine_width
        self._machine_depth: Optional[float] = machine_depth

        self._build_plate_number: Optional[int] = None

        # Results -- read after the finished signal fires
        self._mass: float = 0.0
        self._center_of_mass: numpy.ndarray = numpy.zeros(3, dtype=numpy.float64)
        self._inertia_tensor: numpy.ndarray = numpy.zeros((3, 3), dtype=numpy.float64)

    # -- Getters / setters ------------------------------------------------

    def setBuildPlate(self, build_plate_number: int) -> None:
        self._build_plate_number = build_plate_number

    def getBuildPlate(self) -> Optional[int]:
        return self._build_plate_number

    def getMass(self) -> float:
        return self._mass

    def getCenterOfMass(self) -> numpy.ndarray:
        return self._center_of_mass

    def getInertiaTensor(self) -> numpy.ndarray:
        return self._inertia_tensor

    # -- Main computation (runs on background thread) ---------------------

    def run(self) -> None:
        if not self._layers:
            return

        all_starts: List[numpy.ndarray] = []
        all_ends: List[numpy.ndarray] = []
        all_widths: List[numpy.ndarray] = []
        all_thicknesses: List[numpy.ndarray] = []

        for layer in self._layers:
            for p in range(layer.repeatedMessageCount("path_segment")):
                polygon = layer.getRepeatedMessage("path_segment", p)

                line_types = numpy.frombuffer(polygon.line_type, dtype="u1")

                # Filter to part-material segments only
                mask = filter_part_segments(line_types)
                if not numpy.any(mask):
                    continue

                points = numpy.frombuffer(polygon.points, dtype="f4")
                if polygon.point_type == 0:  # Point2D
                    points = points.reshape((-1, 2))
                else:  # Point3D
                    points = points.reshape((-1, 3))

                # Coordinate transform -- mirror ProcessSlicedLayersJob lines 165-173
                new_points = numpy.empty((len(points), 3), numpy.float64)
                if polygon.point_type == 0:  # Point2D
                    new_points[:, 0] = points[:, 0]
                    new_points[:, 1] = layer.height / 1000  # backend representation -> mm
                    new_points[:, 2] = -points[:, 1]
                else:  # Point3D
                    new_points[:, 0] = points[:, 0]
                    new_points[:, 1] = points[:, 2]
                    new_points[:, 2] = -points[:, 1]

                # mask length == len(line_types) == len(new_points) - 1
                starts = new_points[:-1][mask]
                ends = new_points[1:][mask]

                line_widths = numpy.frombuffer(polygon.line_width, dtype="f4")
                line_thicknesses = numpy.frombuffer(polygon.line_thickness, dtype="f4")

                all_starts.append(starts)
                all_ends.append(ends)
                all_widths.append(line_widths.ravel()[mask].astype(numpy.float64))
                all_thicknesses.append(line_thicknesses.ravel()[mask].astype(numpy.float64))

            Job.yieldThread()  # Allow abort checks between layers

        if not all_starts:
            self._layers = None
            return

        starts = numpy.concatenate(all_starts)
        ends = numpy.concatenate(all_ends)
        widths = numpy.concatenate(all_widths)
        thicknesses = numpy.concatenate(all_thicknesses)

        density = self._get_material_density()

        self._mass, self._center_of_mass, self._inertia_tensor = \
            compute_inertial_properties(starts, ends, widths, thicknesses, density)

        self._apply_build_plate_offset()

        # Free protobuf messages to release memory
        self._layers = None

        Logger.log("d", "Inertial properties computed: mass=%.3fg", self._mass)

    # -- Helpers ----------------------------------------------------------

    def _get_material_density(self) -> float:
        """Return material density in g/cm^3, with PLA fallback."""
        if self._density is not None:
            return self._density

        try:
            global_stack = Application.getInstance().getGlobalContainerStack()
            if global_stack is None:
                return DEFAULT_DENSITY
            extruder_stack = global_stack.extruderList[0]
            density = float(extruder_stack.getMetaDataEntry("properties", {}).get("density", 0))
            if density <= 0:
                Logger.log("w", "Material density is %s, using PLA default %s", density, DEFAULT_DENSITY)
                return DEFAULT_DENSITY
            return density
        except (IndexError, ValueError, TypeError):
            Logger.log("w", "Could not read material density, using PLA default %s", DEFAULT_DENSITY)
            return DEFAULT_DENSITY

    def _apply_build_plate_offset(self) -> None:
        """Apply build plate origin offset to CoM for non-center-is-zero machines."""
        try:
            center_is_zero = self._machine_center_is_zero
            width = self._machine_width
            depth = self._machine_depth

            if center_is_zero is None or width is None or depth is None:
                settings = Application.getInstance().getGlobalContainerStack()
                if settings is None:
                    return
                if center_is_zero is None:
                    center_is_zero = settings.getProperty("machine_center_is_zero", "value")
                if width is None:
                    width = settings.getProperty("machine_width", "value")
                if depth is None:
                    depth = settings.getProperty("machine_depth", "value")

            if not center_is_zero:
                self._center_of_mass[0] -= width / 2
                self._center_of_mass[2] += depth / 2
        except Exception:
            Logger.log("w", "Could not apply build plate offset to center of mass")
