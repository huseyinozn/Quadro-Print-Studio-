# Copy Mode (milestone 3) — results

Copy Mode prints exactly 2 copies of one single-colour model: T0 the left copy, T1 the right copy. T2 and T3 stay parked at their endstop positions for the whole print. The Farm Mode multiple-of-4 rule does not apply.

## Sequence (same order as the successful reference print)

1. Park all heads; T2/T3 targets 0 °C, T0/T1 standby.
2. T0: heat, own straight front prime line, layers 1–5 of the left copy alone. T1 preheat starts while T0 prints layer 5.
3. T0 parks; T1 (already hot, M109 confirms) primes its own front line immediately before printing layers 1–5 of the right copy.
4. Layers 1–5 equalise each head's own mesh + tool offsets to one common height; from layer 6 T0 + T1 print together (T1 in COPY, its extruder synchronised).
5. Slice retract, wipe, z-hop, speeds and accelerations are kept per move (checked per move by the verifier). No old mesh is used.

Layout: model-size driven, centred near the middle of the bed. Synchronised heads keep the 56.25 mm minimum carriage clearance plus a 5 mm margin (OrcaCube: 61.31 mm carriage spacing, 16.67 mm between the cubes); T1 must stay ≥ 56.25 mm from parked T2 (worst 211 mm).

## Results

| Test | Result |
|---|---|
| Phone: OrcaCube → only profile → layout → generate → verify → download | PASS — model input shown: False; copies ['0/0', '1/0']; "Software checks passed"; layers per copy: 225 on all 2; downloaded SHA-256 = verified: True |
| Independent verify of the OrcaCube file | passed: True; layers per copy {'T0 (43.43, 83.4)': 225, 'T1 (104.68, 83.65)': 225}; synchronised from layer 6; foundation order [0, 1]; T2/T3 parked at {'T2': 412.75, 'T3': 469.0}, stayed parked: True, extruded {'T2': 0.0, 'T3': 0.0}; final park: True |
| Phone: Upload your own model (different model: shark) in Copy Mode | PASS — 2 copies · 29.75 × 55.08 mm · 83 layers; layers per copy: 83 on all 2; download hash matches: True |
| Mutation: T2 synchronised by mistake | rejected (exit 3): CARRIAGE_CLEARANCE, CARRIAGE_LIMIT, COLD_EXTRUDE, COPY_COUNT, COPY_INCOMPLETE, COPY_PHASE, FIRST_PRINT_STATE, OVER_PRIME, PARKED_HEAD_ACTIVE, RETRACT_STATE, SYNC_RETRACT_MISMATCH |
| Mutation: layer 120 skipped | rejected (exit 3): COPY_COUNT, COPY_INCOMPLETE, TRANSFER_CLEARANCE; copy (0, (43.43, 83.4)) missing layers [120]; copy (1, (104.68, 83.65)) missing layers [120] |
| Mutation: T1 layer 4 skipped (one copy only) | rejected (exit 3): COPY_COUNT, COPY_INCOMPLETE, TRANSFER_CLEARANCE; copy (1, (104.68, 83.65)) missing layers [4] |
| Farm regression: phone 40 Sharks flow | PASS — marks equal planner: True; G-code SHA-256 unchanged: True |
| Automated tests | 93 tests OK (incl. Copy adversarial: third head synced, parked head moved, skipped foundation / synchronised layer, late COPY entry, count rule; web: OrcaCube example, own model Copy end-to-end, Dual Color refused) |

Dual Color Copy Mode stays "Coming soon" (UI and API refuse it). Software checks passed is a statement about the generated file, not a print on hardware.
