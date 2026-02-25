# Copyright (c) 2026 UltiMaker
# Cura is released under the terms of the LGPLv3 or higher.

"""QObject bridging computed inertial properties from CuraEngineBackend to QML.

Connects to the backend's ``inertialPropertiesChanged`` and ``slicingStarted``
signals.  All numpy values are converted to native Python floats before storage
so that pyqtProperty never exposes non-serialisable types to QML.

**Coordinate convention:**
The backend computes in Cura's internal Y-up system (X right, Y up, Z depth).
The UI presents the standard 3D-printing convention (X right, Y depth, Z up).
This class swaps the Y and Z axes when reading from the backend so that all
pyqtProperties match the user-facing labels.
"""

from PyQt6.QtCore import QObject, pyqtSignal, pyqtProperty, pyqtSlot

from UM.Logger import Logger

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cura.CuraApplication import CuraApplication


class InertialProperties(QObject):
    """Holds inertial property results (mass, CoM, tensor) for QML consumption."""

    inertialPropertiesChanged = pyqtSignal()

    def __init__(self, application: "CuraApplication", parent=None) -> None:
        super().__init__(parent)
        self._application = application

        # Scalar properties -- always native Python floats
        self._mass: float = 0.0
        self._center_of_mass_x: float = 0.0
        self._center_of_mass_y: float = 0.0
        self._center_of_mass_z: float = 0.0
        self._ixx: float = 0.0
        self._iyy: float = 0.0
        self._izz: float = 0.0
        self._ixy: float = 0.0
        self._ixz: float = 0.0
        self._iyz: float = 0.0
        self._has_data: bool = False

        # Connect to backend signals
        self._backend = self._application.getBackend()
        if self._backend:
            self._backend.inertialPropertiesChanged.connect(self._onInertialPropertiesChanged)
            self._backend.slicingStarted.connect(self._onSlicingStarted)

    # -- Signal handlers ------------------------------------------------------

    def _onInertialPropertiesChanged(self) -> None:
        """Read computed results from backend and expose as native floats.

        Swaps Y↔Z to convert from internal Y-up to user-facing Z-up:
          user X = internal X,  user Y = internal Z,  user Z = internal Y

        CoM uses Cura's left-handed frame (matching the move tool display).

        The inertia tensor uses the right-handed G-code convention
        (X right, Y toward back, Z up).  The conversion T' = P T P^T
        where P includes Y-negation gives:
          Ixx stays, Iyy↔Izz swap, Ixy=-Ixz_int, Ixz=Ixy_int, Iyz=-Iyz_int.
        """
        try:
            self._mass = float(self._backend._inertial_mass)

            com = self._backend._inertial_center_of_mass
            if com is not None:
                self._center_of_mass_x = float(com[0])       # X → X
                self._center_of_mass_y = float(com[2])       # Y_user ← Z_internal
                self._center_of_mass_z = float(com[1])       # Z_user ← Y_internal

            tensor = self._backend._inertial_tensor
            if tensor is not None:
                self._ixx = float(tensor[0, 0])              # Ixx stays
                self._iyy = float(tensor[2, 2])              # Iyy_user ← Izz_internal
                self._izz = float(tensor[1, 1])              # Izz_user ← Iyy_internal
                self._ixy = -float(tensor[0, 2])             # Ixy_user ← -Ixz_internal (RH)
                self._ixz = float(tensor[0, 1])              # Ixz_user ← Ixy_internal
                self._iyz = -float(tensor[1, 2])             # Iyz_user ← -Iyz_internal (RH)

            self._has_data = True
        except Exception:
            Logger.logException("w", "Failed to read inertial properties from backend")
            self._has_data = False

        self.inertialPropertiesChanged.emit()

    def _onSlicingStarted(self) -> None:
        """Reset all values when a new slice begins."""
        self._mass = 0.0
        self._center_of_mass_x = 0.0
        self._center_of_mass_y = 0.0
        self._center_of_mass_z = 0.0
        self._ixx = 0.0
        self._iyy = 0.0
        self._izz = 0.0
        self._ixy = 0.0
        self._ixz = 0.0
        self._iyz = 0.0
        self._has_data = False
        self.inertialPropertiesChanged.emit()

    # -- pyqtProperties (camelCase for QML) -----------------------------------

    @pyqtProperty(float, notify=inertialPropertiesChanged)
    def mass(self) -> float:
        return self._mass

    @pyqtProperty(float, notify=inertialPropertiesChanged)
    def centerOfMassX(self) -> float:
        return self._center_of_mass_x

    @pyqtProperty(float, notify=inertialPropertiesChanged)
    def centerOfMassY(self) -> float:
        return self._center_of_mass_y

    @pyqtProperty(float, notify=inertialPropertiesChanged)
    def centerOfMassZ(self) -> float:
        return self._center_of_mass_z

    @pyqtProperty(float, notify=inertialPropertiesChanged)
    def ixx(self) -> float:
        return self._ixx

    @pyqtProperty(float, notify=inertialPropertiesChanged)
    def iyy(self) -> float:
        return self._iyy

    @pyqtProperty(float, notify=inertialPropertiesChanged)
    def izz(self) -> float:
        return self._izz

    @pyqtProperty(float, notify=inertialPropertiesChanged)
    def ixy(self) -> float:
        return self._ixy

    @pyqtProperty(float, notify=inertialPropertiesChanged)
    def ixz(self) -> float:
        return self._ixz

    @pyqtProperty(float, notify=inertialPropertiesChanged)
    def iyz(self) -> float:
        return self._iyz

    @pyqtProperty(bool, notify=inertialPropertiesChanged)
    def hasData(self) -> bool:
        return self._has_data

    # -- Slots ----------------------------------------------------------------

    @pyqtSlot()
    def copyToClipboard(self) -> None:
        """Format inertial properties as text and copy to system clipboard."""
        # Conversion factor: g*mm^2 -> kg*m^2 = 1e-9
        si_factor = 1e-9

        text = (
            f"Mass: {self._mass:.4f} g\n"
            f"\n"
            f"Center of Mass (mm) [Cura frame: X right, Y depth, Z up]:\n"
            f"  [{self._center_of_mass_x:.2f}, "
            f"{self._center_of_mass_y:.2f}, {self._center_of_mass_z:.2f}]\n"
            f"\n"
            f"Inertia Tensor (g*mm^2) [Right-handed frame: X right, Y depth, Z up]:\n"
            f"  Ixx: {self._ixx:.4f}  Ixy: {self._ixy:.4f}  Ixz: {self._ixz:.4f}\n"
            f"  Ixy: {self._ixy:.4f}  Iyy: {self._iyy:.4f}  Iyz: {self._iyz:.4f}\n"
            f"  Ixz: {self._ixz:.4f}  Iyz: {self._iyz:.4f}  Izz: {self._izz:.4f}\n"
            f"\n"
            f"Inertia Tensor (kg*m^2) [Right-handed frame: X right, Y depth, Z up]:\n"
            f"  Ixx: {self._ixx * si_factor:.10e}  Ixy: {self._ixy * si_factor:.10e}  "
            f"Ixz: {self._ixz * si_factor:.10e}\n"
            f"  Ixy: {self._ixy * si_factor:.10e}  Iyy: {self._iyy * si_factor:.10e}  "
            f"Iyz: {self._iyz * si_factor:.10e}\n"
            f"  Ixz: {self._ixz * si_factor:.10e}  Iyz: {self._iyz * si_factor:.10e}  "
            f"Izz: {self._izz * si_factor:.10e}\n"
        )

        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
            Logger.log("d", "Inertial properties copied to clipboard")
        else:
            Logger.log("w", "Could not access system clipboard")
