# New two-colour fidget slice (`dual-color-mirror-printing`): result with the current profile

All values are **calculated** from the slice, the current profile (mesh and offsets) and the generated commands. Nothing was measured on the machine.

## Result

**Refused.** Download stays closed. Two independent blockers remain, both caused by the current tool offsets:

| Check | Result | Criterion |
|---|---|---|
| Colour mapping (geometry) | **passes** | right region = left region translated, every layer |
| Pair printing T0+T1, then T2+T3 | **passes** (verifier: pairs from machine state, layer 4 on) | – |
| Height (Z) at colour borders | **blocked** | layer thickness 0.080–0.300 mm; nozzle at most 0.03 mm below other material |
| Colour alignment (Y) | **blocked**: 0.209 mm on both models | ≤ 0.100 mm |
| Travels over printed material, parks, carriage clearance | **pass** | see below |

**Cross-check (test only, never offered):** the same file with offsets that are consistent in Z and Y (T3 derived from T0/T1/T2) generates and passes verification, with all four heads at 100/100 layers. Nothing else in this file blocks.

## 1. Colour mapping

The slice has the left model on T0/T1 and the right model on T2/T3. The reader assigns sides by position and finds each left region's counterpart by **geometry**, never by tool number. A counterpart must have the same deposited area, shifted by one vector, on all 100 layers.

| Physical head | Slice tool | Model | Colour | Prints with |
|---|---|---|---|---|
| T0 | T0 (#0000FF in the slice) | left | 1 | T1 (pair A, first) |
| T1 | T2 (#00FF00) | right | 1 | T0 (COPY) |
| T2 | T1 (#FF0000) | left | 2 | T3 (pair B, second) |
| T3 | T3 (#FFFF00) | right | 2 | T2 (COPY) |

* T0 ↔ T2 and T1 ↔ T3 match exactly: translation (97.749, 1.539) mm, 0 % mismatch on 100/100 layers.
* T0 ↔ T3 and T1 ↔ T2 do not match (about 99 % mismatch).
* The model is not mirrored; it is a pure translation.
* The slice gives the corresponding regions different filament colours (left colour 1 #0000FF, right colour 1 #00FF00). The physical colour is whatever is loaded in each head.
* A user's own file is never changed silently: if the right regions do not correspond, it is refused with `DUAL_NOT_COPYABLE`.

## 2. Y: 0.209 mm colour shift (current profile)

The current offsets give T2−T0 = −0.0500 mm and T3−T1 = +0.3688 mm. Both pairs share one Y, so the best split leaves colour 2 shifted **+0.209 mm** (left) and **−0.209 mm** (right) against colour 1. Matching Z does not change this.

### Effect at the real colour borders

Each colour's bead footprints (half line width) are rasterised as sliced and as planned; the difference gives the new gap and overlap.

| | Layer 14, left model | Layer 14, right model | All layers, left / right |
|---|---|---|---|
| New gap at the colour border | 1.38 mm², widest 0.22 mm along Y at (81.19, 173.02) | 1.41 mm², widest 0.22 mm at (168.21, 164.09) | 76.2 / 78.5 mm² (widest 0.25 mm at 0.05 mm raster) |
| New overlap at the border | 0.10 mm², widest 0.22 mm at (85.37, 147.20) | 0.10 mm², widest 0.22 mm at (172.33, 148.29) | 3.6 / 3.7 mm² |
| Overlap in the slice (as sliced) | 0.05 mm² | 0.05 mm² | 1.7 / 1.7 mm² |
| Step on the outer surface where colour 2 meets the outline | 29.0 mm² | 30.5 mm² | – |

Layer 14 is measured on a 0.02 mm raster, all layers on 0.05 mm. On the left model, colour 2 moves 0.209 mm in +Y. A gap of up to ~0.22 mm opens wherever colour 2 lies on the −Y side of colour 1, and extra overlap forms on the +Y side. The right model has the same effect with the sign reversed.

### Against the absolute alignment target (each model's planned position)

| Pair-B Y choice | Left model: colour 2 off target | Right model: colour 2 off target |
|---|---|---|
| As planned (split) | +0.209 mm | −0.209 mm |
| Left exact | 0.000 mm | −0.419 mm |
| Right exact | +0.419 mm | 0.000 mm |

In all three cases colour 1 (T0, T1) sits exactly on target. Because the shared-Y difference is fixed at 0.419 mm, no Y choice gets both models within 0.100 mm.

## 3. Z, travels and parks (same placement, independent verifier)

The file was generated for checking only, never offered, and checked by the separate verifier.

* **Z (blocked).**
  * Left model: colour 2 is 0.119 mm lower than colour 1.
    * At layer 14, T0 at (126.74, 148.12) is **0.319 mm** thick on colour 2 (T2) of layer 13.
    * At colour borders, the bead edge is 0.303–0.319 mm thick (10 381 cells, layers 3–73).
    * T2 prints up to **0.119 mm** below colour 1 beside it (10 875 points). At layer 14, T2 at (127.38, 147.71) is 0.1188 mm into colour 1.
    * **262 wipes and travels** drag through colour 1. Example: layer 14, T2 wipe, slicer line 11133 at (85.62, 147.35), 0.118 mm.
  * Right model: colour 2 is 0.119 mm higher. Colour 2 on colour 1 is 0.318 mm thick (230 cells; first at layer 12, T3 at (168.68, 173.26)). There are no sinks and no travels through colour 1.
  * Verifier, independently:
    * `COPY_THICKNESS` (measured 0.092–0.276 mm against the nominal 0.200);
    * `DESCENT_INTO_MATERIAL` (T2 descends 0.118 mm into material);
    * `FOUNDATION_FLOW` in layers 2–3 at colour borders (E computed for a thickness that the other colour's height changes);
    * `DUAL_REGISTRATION` 0.209 mm on both models.
* **Passing:**
  * colour regions 100/100 on T0–T3;
  * pairs [T0, T1] then [T2, T3] from layer 4;
  * towers and prime lines correct;
  * no idle head moves towards the printing heads;
  * first layer within 5 µm of 0.200 mm above the mesh;
  * travels between parts ≥ 1.000 mm above printed material;
  * in-part travel at least 0.075 mm above its own completed layers;
  * parallel park sweeps ≥ 5.0 mm above printed material;
  * carriage spacing ≥ 56.25 mm (exactly the minimum at the endstop parks, park margin 0.0);
  * synchronised pairs 86.79 mm apart;
  * pair A 178.2 mm from parked T2, pair B 176.7 mm from parked T1;
  * final state safe.

## Remaining blockers for this file

1. **Z:** the shared-Z residual of 0.119 mm (T2−T0 = +0.106 mm, T3−T1 = −0.131 mm). It causes 0.319 mm colour-change thickness, the nozzle running 0.119 mm below colour 1 beside it, and travels and wipes through colour 1.
2. **Y:** a 0.209 mm colour shift on both models, which opens gaps of up to 0.22 mm and overlaps at the colour borders.

Both come only from the current tool offsets; geometry, mapping, motion, parks and clearances pass. No tolerance was changed and no single-head fallback was used.
