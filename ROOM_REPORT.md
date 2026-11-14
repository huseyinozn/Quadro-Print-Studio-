# Dual Color Copy Mode: room for the filament where colours stack (from V5, commit 7a09cc2)

## Result for the current fidget slice and the user's current profile

**Not possible with the existing paths and one shared Z and one shared E per pair.** The plan is refused, no G-code is offered and download stays closed (403). No limit was loosened, there is no single-head fallback, and mesh and offsets are unchanged.

V5 limits stay as they were: Y 0.30 mm, Z deviation 0.30 mm, into-material / travel 0.03 mm. The new check uses the product's existing minimum layer thickness of **0.08 mm** (0.2 × nozzle) as the room needed under a bead. It is planned in `dualroom.py` (`colour_stack_room`) and checked independently by the verifier (`DUAL_STACK_ROOM`).

### Room as planned in V5 (planner replay; the verifier finds the same on the V5 file)

| | Left model | Right model |
|---|---|---|
| Colour 1 on colour 2 (pair T0+T1), under the bead | 0.218 mm | **−0.019 mm** (layers 14–17, 31–40, 63–71; e.g. layer 16, T1) |
| Colour 2 on colour 1 (pair T2+T3), under the bead | 0.181 mm | 0.419 mm (layers 20, 21, 31, 71) |
| At continuing colour borders (bead edge) | ≥ 0.218 mm | **−0.019 mm** (layers 4–73) |

The verifier on the V5 G-code reports right centre −0.0188 mm (T1, layer 16) and edge −0.0191 mm. `DUAL_STACK_ROOM` is raised 87 times.

## Why local layer compensation cannot reach the goal here

The planner now applies local layer compensation automatically: per layer it raises pair A and/or pair B, scales E with the local room, and returns to the slice's target Z over the following layers. Three conditions cannot all hold at once:

1. **One E for both models of a pair (the extruders are synchronised).**
   * At any model point where one colour is printed on the other, the room on the right model differs from the left model by exactly **0.2375 mm**. That is the Z-offset mismatch (T3−T1) − (T2−T0) = −0.13125 − 0.10625.
   * The pair's shared Z moves both models together, so no Z choice removes this difference.
   * Example, layer 14, **T0+T1**: with the right room raised to 0.081 mm, the left room is 0.318 mm. Left needs 149 % and right 43 % of the slice's filament per mm: a 3.46× difference for one E command.
   * Example, layers 20/21, **T2+T3**: left 0.151 mm, right 0.389 mm, i.e. 78 % against 175 %.
   * 20 layer/pair cases in total. The accepted flow error is 1.7 % (the verifier's existing flow tolerance).
2. **Enough room under the bead on the right model.**
   * Layer 14 needs a raise of +0.10 mm (0.30 mm layer, the maximum layer thickness).
   * Because colour 1 is printed on colour 2 on consecutive layers (14–17, 33–36, 63–71), the raises add up: to 0.37 mm by layer 17, and to the 0.6 mm search bound by layer 39–40 and 67–71.
   * The room still misses at layers 40, 67–71 (right model still −0.009 mm).
   * Returning is slow (about 0.02 mm per layer), because a faster return pushes the right model's border edge below −0.03 mm. The target Z is reached again at layer 78.
3. **No negative room at continuing colour borders (right model, layers 4–73).**
   * Colour 1 (T1) runs along colour 2 (T3), which stands 0.219 mm higher on the right. At the same time, colour 2 (T2) on the left must stay within 0.03 mm of colour 1 (T0) beside it.
   * Over a run of layers, the per-layer raises cancel in these two conditions. The best possible average room at the right border edge is therefore h − 0.2375 + 0.03 = **−0.0075 mm**, whatever the per-layer heights are.

**What would make it possible:** Z tool offsets with T3−T1 within a few µm of T2−T0 (the 0.2375 mm difference is what blocks). The same file with Z/Y-consistent offsets (test only) was generated and passed verification in V4/V5. Re-slicing is not required.

## What was built (general Dual planner behaviour, Farm and Copy unchanged)

* `dualz.analyse_z` measures per layer and per model the room under the bead centre and at the bead edge, with location and slicer line.
* `dualroom.analyse_room` computes:
  * the room as planned;
  * local layer compensation, i.e. pair-A and pair-B heights per layer above layer 3, within these limits: layer 0.08–0.30 mm; Z deviation ≤ 0.30 mm; nozzle at most 0.03 mm into the other colour beside or below it, also at edges; back on target Z by the last layer;
  * the E needed per model, checked against one synchronised E;
  * the structural bound at continuing borders.
* Planner check `colour_stack_room`: blocking, with the room per model, the layers and pairs that conflict, and the conditions.
* Verifier `DUAL_STACK_ROOM`: room under the bead centre must be ≥ 0.08 mm and at the edge ≥ 0. Computed on the generated G-code (0.1 mm raster, physical tip heights).
* Web:
  * the layout shows "Room for the filament" (smallest/largest room per model, at borders, with compensation, and the conflicting layer/pair table);
  * the result screen shows the smallest room for both models when a file passes.
* G-code with compensation is not produced. No input that needs compensation can pass the synchronised-flow condition, because the need comes from the same model-to-model difference that breaks it.

## Checks

* **Tests:** 130 OK. New tests:
  * room conditions on mismatched offsets (difference 0.23 mm, the synchronised-flow conflict);
  * the verifier flags only `DUAL_STACK_ROOM` on such a file;
  * consistent offsets keep enough room;
  * the web example is refused with the room values shown.
* **Farm:** 40 Sharks output byte-identical (9e113ffd…). Copy behaviour unchanged.
* **Phone flow:** upload of the fidget slice + current profile is refused with "Room for the filament": left 0.181 mm, right −0.019 mm. There is no generate button and download returns 403.

All values are calculated, not measured on the machine.
