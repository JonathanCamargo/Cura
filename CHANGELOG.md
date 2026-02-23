# Inertial Properties Estimator for Cura

Estimates mass, center of mass, and inertia tensor of 3D printed parts from actual sliced toolpath data. Results appear in the print info panel after slicing.


### Usage

1. **Open Cura** and load your STL/3MF model as usual.
2. **Configure print settings** (infill %, layer height, line width, material).
3. **Slice** the model by clicking the "Slice" button.
4. After slicing completes, open the **print info panel** (the information icon next to the slice button in the bottom-right action panel).
5. A new **"INERTIAL PROPERTIES"** section appears below the material estimation, showing:
   - Mass (g)
   - Center of mass X, Y, Z (mm) in Cura's global coordinate frame (Y-up)
   - Inertia tensor diagonal: Ixx, Iyy, Izz (g*mm^2)
   - Inertia tensor off-diagonal: Ixy, Ixz, Iyz (g*mm^2)
6. Click **"Copy to clipboard"** to export all values as formatted text including both g*mm^2 and kg*m^2 units.

### Notes

- The computation runs automatically in the background after every slice. No extra steps required.
- Results clear automatically when you re-slice or change models.
- Only **part lines** contribute to the computation (walls, skin, infill). Support material, skirt, prime tower, and travel moves are excluded.
- Material density is read from the active material profile. Change the material density in the profile to match your filament.
- The inertia tensor is computed **about the center of mass** in the **global frame** as viewed in the displayed axes X RED-Y GREEN-Z BLUE

---

## What Changed

### New Files

| File | Lines | Purpose |
|------|-------|---------|
| `cura/InertialComputation.py` | 126 | Standalone numpy computation engine. Computes mass, center of mass, and inertia tensor from segment arrays. Zero Cura/Uranium dependencies. |
| `cura/ComputeInertialPropertiesJob.py` | 192 | Background job (UM.Job) that parses raw protobuf LayerOptimized messages, applies coordinate transforms, and calls the computation engine. |
| `cura/UI/InertialProperties.py` | 170 | QObject bridge exposing 11 pyqtProperties (mass, 3 CoM, 6 tensor, hasData) and a copyToClipboard slot to QML. |
| `tests/TestInertialComputation.py` | 407 | 14 tests validating mass, CoM, inertia, line type filtering, precision, and performance. |

### Modified Files

| File | Change |
|------|--------|
| `plugins/CuraEngineBackend/CuraEngineBackend.py` | Added `inertialPropertiesChanged` Uranium Signal, inertial job trigger in `_onSlicingFinishedMessage`, result storage attributes, clear-on-reslice handler. |
| `cura/CuraApplication.py` | Instantiates `InertialProperties` in `__init__` and registers it as QML context property `"InertialProperties"`. |
| `resources/qml/ActionPanel/PrintJobInformation.qml` | Added 109-line "Inertial properties" section with mass, CoM, tensor display, and copy-to-clipboard button. |

### Signal Chain

```
CuraEngine slice completes
  -> CuraEngineBackend._onSlicingFinishedMessage()
    -> ComputeInertialPropertiesJob.start()  [background thread]
      -> Parses protobuf, calls compute_inertial_properties()
    -> Job.finished signal
      -> _onInertialPropertiesJobFinished()  [main thread]
        -> Stores results, emits inertialPropertiesChanged Signal
          -> InertialProperties._onInertialPropertiesChanged()
            -> Converts numpy to Python floats, emits pyqtSignal
              -> QML property bindings update display
```

---

## Example: 20mm Calibration Cube

A standard 20mm calibration cube printed in PLA (density 1.24 g/cm^3).

### Analytical Reference (Uniform Solid Cube)

For a solid cube with side length `a = 20 mm` and uniform density `rho`:

```
Mass (solid)  = a^3 * rho = 8000 mm^3 * 1.24 g/cm^3 / 1000 = 9.92 g
Center of Mass = (10.0, 10.0, 10.0) mm   (geometric center)
Ixx = Iyy = Izz = (1/6) * m * a^2 = (1/6) * 9.92 * 400 = 661.33 g*mm^2
Ixy = Ixz = Iyz = 0   (symmetry)
```

### Uniform Density Approximation at 20% Infill

A naive approach estimates inertial properties by scaling solid values by an effective fill fraction. With 20% infill, 2 walls (0.4mm each), 0.2mm layer height, and 4 top/bottom layers:

```
Cube volume:    8000 mm^3
Wall volume:    4 faces * 20mm * 20mm * 0.4mm = 640 mm^3  (8.0%)
Top/bottom:     2 faces * 20mm * 20mm * 0.8mm = 640 mm^3  (8.0%)
Infill core:    (20 - 0.8)^2 * (20 - 1.6) * 0.20 = 1348 mm^3 (16.9%)
Effective fill: ~33% of solid volume

Approx mass:       9.92 * 0.33 = 3.27 g
Approx Ixx = Iyy = Izz = 661.33 * 0.33 = 218.24 g*mm^2
```

This uniform-density approximation assumes mass is evenly distributed throughout the fill fraction, which is **wrong** because:

- Walls and top/bottom layers are **concentrated at the surfaces** (farther from CoM)
- Infill is **concentrated in the interior** (closer to CoM)
- The actual mass distribution is **not uniform**

### Toolpath-Based Estimate (This Feature)

After slicing the 20mm cube in Cura with the same settings, the inertial properties estimator computes from the **actual toolpath segments**:

```
Expected mass:  ~3.2-3.5 g  (depends on exact slicer path planning)
Center of Mass: ~(10.0, 10.0, -10.0) mm  (center, with Z negated per Cura convention)
Ixx, Izz:      > uniform approximation  (walls at surfaces increase rotational inertia)
Iyy:           > uniform approximation  (top/bottom layers at extremes increase Iyy)
Ixy, Ixz, Iyz: ~0  (symmetric geometry)
```

### Why the Toolpath Estimate is More Accurate

| Property | Uniform Density Approx | Toolpath Estimate |
|----------|----------------------|-------------------|
| Mass | Approximate (depends on assumed fill fraction) | Exact sum of all extruded segment volumes * density |
| CoM | Assumes geometric center | Mass-weighted centroid of actual extrusion paths |
| Inertia | Underestimates (ignores mass distribution) | Accounts for walls at surfaces, infill patterns, top/bottom layers |

**Key insight:** For a 20% infill cube, the inertia tensor from actual toolpaths will be **larger** than the uniform-density approximation because the dense walls and top/bottom layers are located at the surfaces (maximum distance from CoM), contributing disproportionately to rotational inertia. The uniform approximation spreads that mass evenly, underestimating the contribution of surface material.

The error grows with:
- Lower infill percentages (more contrast between shell and core)
- Larger parts (greater distance from surface to CoM)
- Asymmetric infill patterns (e.g., gyroid vs. grid)

---

## Limitations

- **Approximation model:** Each extrusion segment is modeled as a rectangular-cross-section prism (width x thickness x length). This slightly overestimates volume at path corners and underestimates at overlaps.
- **Single material density:** Uses the density from extruder 0's material profile for all segments. Multi-extruder prints with different materials are not yet supported.
- **Global frame only:** The inertia tensor is reported in Cura's global coordinate frame (Y-up). To get inertia in a different frame, rotate the part in the scene before slicing.
- **No mesh comparison:** The estimate is based solely on toolpaths, not on the original mesh geometry. This is by design -- it reflects what the printer actually deposits.
