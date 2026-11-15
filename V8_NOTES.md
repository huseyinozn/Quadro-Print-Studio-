# V8: small negative room (< 0.050 mm) accepted as a warning

**Rule** (`dualtol.small_negative`, every model and profile, planner and verifier):
* On an **extrusion** move where the colours are printed on top of each other or their borders meet, a local room that is negative but smaller than 0.050 mm in size is an accepted quality deviation. Exactly 0.050 mm or more is not.
* The rule applies to the same small deviation wherever it is reported: minimum room, nozzle below the other colour's surface while extruding, and no common E at that point. The move keeps the slice E (100 %); the warning shows both heads' need.
* Not covered by the rule:
  * a flow mismatch where both rooms are positive;
  * a positive room below 0.08 mm;
  * travel, wipe and descent (0.03 mm), park and carriage checks.

**Codes**
* Planner: warning `PLAN_SMALL_NEGATIVE_ROOM`.
* Verifier: warning `DUAL_SMALL_NEGATIVE_ROOM`; errors `DUAL_STACK_ROOM`, `DUAL_INTO_MATERIAL` (extrusion), and the new `DUAL_SYNC_FLOW` (per piece of a pair: one E must fit both heads).

**Fidget + current profile**
* The −0.019 mm room is no longer a reason to refuse. It appears as a warning: 109 points on 44 synchronised moves, slice E kept. Example: layer 14, T0+T1, slicer line 14607, left needs 108 %, right has −0.019 mm.
* Independent layers: 3; pairs from layer 4.
* **Remaining independent blocker:** a flow mismatch with both rooms positive. First case: layer 15, T0+T1, slicer line 16276 (outer wall). Left (127.10, 147.40) is on colour 2 with room 0.219 mm and needs 108 %; right (214.08, 147.65) is on colour 1 with room 0.200 mm and needs 100 %.
  * Size: 46 moves in 19 layer/pair turns.
  * The verifier independently reports the same at G-code line 38412 (`DUAL_SYNC_FLOW`). It also finds layer 31 T2+T3: rooms 0.182 / 0.419 mm, needing 92 % / 181 %.
* No G-code is offered and download returns 403.

**Synthetic check of the full flow:** a mismatched-offset model whose only deviation is a small negative room (−0.015 mm) goes plan → generate → verify (passes with warnings) → download. The downloaded SHA-256 equals the verified one.

**Tests:** 139 OK.
* Boundary at −0.049 / −0.050 / −0.051 mm in the planner and in the verifier: accepted / not / not.
* Travel limit unchanged at 0.03 mm.
* Farm output byte-identical.
