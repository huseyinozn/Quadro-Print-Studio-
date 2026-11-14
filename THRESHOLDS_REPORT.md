# Dual Color Copy Mode: owner limits Y 0.30 mm and Z 0.30 mm

## Limits (one definition: `quadro_studio/dualtol.py`, used by the planner and the independent verifier)

| Limit | Value | Meaning | Comparison |
|---|---|---|---|
| `y_registration` | **0.30 mm** (owner) | absolute Y offset between the two colours of one model | ≤ passes |
| `z_deviation` | **0.30 mm** (owner) | absolute deviation of the local layer thickness from nominal where one colour is printed on the other | ≤ passes |
| `into_material` | 0.03 mm (unchanged) | how far a nozzle may be below material of the other colour while extruding, descending, travelling or wiping | ≤ passes; **separate check, not relaxed** |

Equal to the limit passes. An allowance of 0.0001 mm only absorbs G-code rounding (XY 4 decimals, Z 5 decimals). Mesh and offsets are used exactly as measured.

Where the checks run:
* **Planner:** `pair_y_registration`, `pair_z_consistency`, `into_material` and `travel_drag` (four separate checks with value and limit).
* **Verifier:** `DUAL_REGISTRATION`, `DUAL_Z_DEVIATION`, `DUAL_INTO_MATERIAL` and `DUAL_TRAVEL_DRAG`, computed on a 0.1 mm raster of the generated file. Layer thickness against the own colour's previous layer stays at the strict 0.004 mm tolerance.

## New fidget slice + current profile

**Planned, generated, independently verified (0 errors), download open.** Pair printing T0+T1, then T2+T3 is kept from layer 4.

| Check | Value | Limit | Result |
|---|---|---|---|
| Y colour alignment, left / right | 0.209 / 0.209 mm | 0.30 | pass |
| Z deviation where colours stack | 0.219 mm (right), 0.060 mm (left) | 0.30 | pass |
| Nozzle below the other colour (deepest) | 0.019 mm | 0.03 | pass |
| Travel / wipe below the other colour (deepest) | 0.019 mm | 0.03 | pass |
| Colour regions T0–T3 | 100/100 layers each | – | pass |
| Parks, carriage and travel clearances | parks 56.25 mm (the minimum), park sweeps 5.0 mm, travels 1.0 mm above parts | – | pass |

## What blocked with the plain (symmetric) Z split, and the fix chosen

With the symmetric split, colour 2 sits ±0.119 mm against colour 1. That **passes** the new Z limit (0.119 ≤ 0.30). The separate checks still blocked:

| Check (symmetric split) | Planner | Independent verifier |
|---|---|---|
| Nozzle into the other colour (extruding) | left model: T2 prints up to 0.119 mm below colour 1 beside it, 10 875 points, layers 2–73 (e.g. layer 14, T2 at (127.38, 147.71)) | `DUAL_INTO_MATERIAL` 155 (deepest 0.119 mm), `DESCENT_INTO_MATERIAL` 1 |
| Travels / wipes through the other colour | 391 moves (e.g. layer 14, T2 wipe, slicer line 11133) | – |

### Fix 1: raised travel

Implemented and verified. Travel and wipe moves that would pass through the other colour (490 slice moves, including a 0.1 mm wider safety footprint) are lifted by the depth + 0.05 mm, and lowered again before the next move.

Result with the symmetric split: `DUAL_TRAVEL_DRAG` = 0 in the verifier. The extrusion problem **remains** (T2 beside the higher colour 1), so the symmetric split stays blocked.

### Fix 2: pair-B height, applied automatically

Pair B is printed 0.0999 mm higher. Offsets and mesh are unchanged; only the shared pair-B Z is chosen differently. This keeps every nozzle within 0.019 mm of the other colour:

* left model: colour 2 −0.019 mm;
* right model: colour 2 +0.219 mm.

All checks pass, so no raised travel is needed.

**Cost (shown in the preview):**
* The Z deviation is 0.219 mm on the right model instead of 0.119 mm.
* Where colour 1 is printed on colour 2 of the layer below on the right model, the local layer is −0.019 to +0.036 mm thick. The nozzle is up to 0.019 mm below colour 2's top, which is within the unchanged 0.03 mm into-material limit.
* At those spots, colour 1 is deposited with almost no room.

The symmetric split can still be forced (`DualOptions(z_split="symmetric")`); it is then blocked as described above.

## Tests (128 OK)

* **Limit rule:** `within()` at 0.29 / 0.30 / 0.31.
* **Y, planner and verifier:** offsets giving exactly 0.29 / 0.30 / 0.31 mm → pass / pass / fail. The verifier measured 0.29 / 0.30 / 0.31 ± 0.0002 mm from the deposits.
* **Z, planner** (replay with pair B 0.29 / 0.30 / 0.31 mm lower): `z_deviation` blocks only at 0.31. `nozzle_into_material` stays blocking in all three cases.
* **Z, verifier** (generated file with pair B lowered 0.29 / 0.30 / 0.31 mm): `DUAL_Z_DEVIATION` only at 0.31. `DUAL_INTO_MATERIAL` is present in all three cases.
* **Splits:** symmetric split blocked by the into-material check (planner and verifier); the chosen split passes. Raised travel leaves no drag in the verifier and alignment is intact.
* **Regression:** the Farm 40 Sharks output is byte-identical (SHA-256 9e113ffd…).

All values are calculated. No printer measurement was made.
