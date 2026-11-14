# V7: shared E checked point by point; independent layers chosen automatically

## 1. Shared E on the same move and the same point (`dualroom.sync_flow_points`)

Both models are replayed together, one pair turn after the other. For every point (every 0.1 mm) of every extrusion move that a synchronised pair prints at the same time:

* the room under the nozzle is computed on both models at that slice point;
* each head gets the E interval it needs there: A(room, w) / A(h, w) of the slice E, ±1.7 %, the verifier's existing flow tolerance;
* a common E is chosen when the two intervals overlap; otherwise it is a conflict.

Points over older layers (sparse-infill gaps, bridges) keep the slice's E and are not judged. The negative-room, into-material and travel checks are unchanged.

**Current fidget + current profile:** 2 650 117 points are printed directly on the layer below; 2 649 918 of them have a common E.

* **First real conflict:** layer 14, pair **T0+T1**, slicer line **14607** (outer wall).
  * As planned: left (85.33, 147.68) on colour 2, room 0.2185 mm (needs 108 %); right (172.31, 147.93) on colour 2, room −0.0187 mm (no room).
  * With local layer compensation: left 0.319 mm needs 148 %, right 0.081 mm needs 43 %. There is no common E.
* **Size:** 83 moves (199 points) in 28 layer/pair turns. The first are 14–17 T0+T1 and 20–21 T2+T3. The largest flow ratio is 2.0× as planned.
* **Two kinds of conflict:**
  * both models on the other colour: the rooms differ by the Z-offset mismatch, 0.2375 mm;
  * the 0.209 mm Y shift puts one model on colour 1 and the other on colour 2. Example: layer 15, line 16276, left 0.319 mm against right 0.290 mm.

## 2. Independent layers: default 3, automatic minimum

The planner tries 3, 4, … layers and keeps the first count for which every head's own blend from its measured bed height to the common height stays within 0.08–0.30 mm. The limit is 10 layers or a quarter of the model, so most of the print never turns into single-head printing. The count is shown in the layout, written in the G-code header, and read by the verifier from that header.

* Current fidget + current profile: **3 layers are enough** (0.144–0.256 mm), so pairs start at layer 4.
* Test with a steep bed: 3 layers fail; the planner chooses the smallest sufficient count, generates and verifies; pairs start at that count + 1.

## Result

There is still **no applicable compensation**: the shared-E conflict above remains. On top of it, two room conditions stay unmet:
* the right model's continuing colour borders cannot get room ≥ 0 (best average −0.0075 mm);
* the raises needed on consecutive layers add up and still miss layers 40 and 67–71.

No G-code is produced and download stays closed. Limits, mesh and offsets are unchanged.

## Firmware: can the two extruders get different amounts while moving together?

* **Not with the supplied firmware and configuration.**
  * `quadro_parallel_carriage.py` registers only `QUADRO_RUN_TOOLCHANGE` and `QUADRO_PARALLEL_MOVE`.
  * The macros use `SYNC_EXTRUDER_MOTION` and `ACTIVATE_EXTRUDER` only. A synchronised extruder follows the primary's E stream.
  * `M221` / `extrude_factor` acts on that one E stream, so it scales both extruders equally.
* **The only per-extruder ratio is `rotation_distance`** (config: extruder 4.516, extruder1 4.483, extruder2 4.529, extruder3 4.406 mm).
  * Mainline Klipper can change it at runtime with `SET_EXTRUDER_ROTATION_DISTANCE`. Klipper's extruder module is not among the supplied files, so this is unverified for this printer.
  * In mainline Klipper the command flushes step generation, so motion stops at every change. That is not usable per move during continuous synchronised printing, and the output dialect and the verifier do not model it.
* **Even with per-extruder flow**, only the E conflict would go away. The room conditions above would still fail.

## Checks

* 134 tests OK, including:
  * point-by-point sync (a conflict exists for mismatched offsets; consistent offsets have a common E everywhere);
  * automatic independent-layer count (3 by default; steep bed → smallest sufficient, generated and verified).
* Farm output byte-identical.
* Phone: the layout shows the conflict table (layer · pair · slicer line, room and E for left / right) and "Independent layers: 3". Download returns 403.
