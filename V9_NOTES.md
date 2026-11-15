# V9: flow mismatch with enough room on both heads is a quality warning

**Rule** (every model and profile; planner and verifier decide the same way):
* Both heads of a synchronised pair have positive room of at least 0.08 mm, and the only mismatch is the filament amount each model would need. This is a warning, not a blocker; both heads use the slicer's E for that move.
* The 50 µm small-negative-room rule is kept.
* A positive room below 0.08 mm stays blocking.
* Motion, park, temperature and machine limits are unchanged.

**Codes:** planner warning `PLAN_FLOW_MISMATCH`, verifier warning `DUAL_FLOW_MISMATCH`. A mismatch where one room is below 0.08 mm is still the error `DUAL_SYNC_FLOW` (or `DUAL_STACK_ROOM`).

**Result screen:** the heading reads "Verified with warnings", and the screen shows the number of affected moves and the largest flow difference between the two models.

**Current fidget + current profile:** planned → generated → independently verified → downloaded with the normal Download button.
* Independent layers: 3; pairs T0+T1, then T2+T3, from layer 4.
* Verifier warnings:
  * `DUAL_FLOW_MISMATCH` on 11 synchronised G-code moves. Largest difference 89.7 % (1.98×) at layer 31, T2+T3, G-code line 80971: room 0.182 / 0.419 mm, needs 92 % / 182 %.
  * `DUAL_SMALL_NEGATIVE_ROOM`: right model −0.019 mm.
* Planner, judged per 0.1 mm point: 46 slicer moves, largest difference 91.6 %. The same decision.
* Colour regions 100/100 on all heads. No errors.
* Downloaded file `dual-color-mirror-printing_dual_2x.gcode`, SHA-256 2882edd2…5dad1c5a. It equals the verified file. It was generated fresh from the slice and the profile; it is byte-identical to the earlier output because the path plan did not change, only the decision rules.

**Tests:** 140 OK. New: flow warning in planner and verifier (same decision and size). The room checks stay blocking. Farm output byte-identical.
