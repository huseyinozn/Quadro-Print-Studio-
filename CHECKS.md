# What every check means (and what it does not prove)

All checks below are performed by `quadro_studio/verify.py`, which re-reads the
output G-code with its own interpreter and never imports the generator. A check
proves a property of the **commanded** motion under the stated machine model; none
of them proves physical behaviour (missed steps, real bed shape, nozzle wear).

## Input stages (inspect / plan / generate refuse on failure)

| Code family | Meaning | Not proven |
|---|---|---|
| `PROFILE_*` | profile archive is safe (bounded size/entries/ratio, no `..`, absolute, symlink, device entries), contains printer.cfg + includes, four carriages, four extruders, part fans, `quadro_parallel_carriage`, exactly one selectable saved mesh, plausible T0..T3 offsets (T0 = 0) | that the mesh/offsets are current on the machine |
| `MESH_DEVIATING_POINT` (warning) | a saved mesh point deviates from the median of its neighbours by more than the threshold (0.25 mm); evaluated on this printer's mesh; the value is used unchanged | whether the bed really has that shape |
| `SLICE_*` | the slice is a Co Print Slicer Klipper export with relative E, no arcs/firmware retraction, one object, T0 only, constant temperatures; every command is classified; unsupported commands are named with their exact limitation | that the slicer settings are good for the material |
| `PLAN_*` | copies fit the printable area and the measured mesh, deposited footprints (convex hulls) keep ≥ min gap, each head reaches its zone with the others parked, COPY carriage spacing ≥ min clearance, Y range, foundation thickness within [0.2, 0.75]·nozzle; copies depending (> 0.01 mm) on a deviating mesh point are listed | optimal packing |

## Output verification (`verify`)

| Check | Meaning | Not proven |
|---|---|---|
| Visit alignment | every model move of every copy/layer is present, in order, on the translated slicer path (pieces on the segment within 0.002 mm), nothing extra | — |
| F per piece | every piece carries the slicer feedrate of its move | real speed (Klipper may limit) |
| Acceleration per move | the (ACCEL, ACCEL_TO_DECEL) state in force for every model move equals the slicer's modal state | input shaping effects |
| Pressure advance | at every extrusion the extruder's PA equals the slice value | — |
| E (layer 1, COPY layers) | E per slicer move equals the slice (rounding only) | filament diameter / flow calibration |
| E (foundation, direct spec) | E per move vs A(h,w)/A(h0,w) at the independently measured local thickness; per layer/head totals, signed and absolute error, extrema | — |
| Layer-1 height | nozzle tip − measured mesh = first layer height at every layer-1 extrusion | real bed height where the mesh is interpolated |
| Local thickness | plane fit of the previous layer of the same copy; COPY layers nominal ± 0.004 mm, foundation within limits | — |
| Common foundation top | the four heads of a COPY group end the foundation at one commanded Z | — |
| Retraction state | filament retract level is 0 at every extrusion, never over-primed, equal on synchronised extruders | oozing during parking |
| Heating | extrusion only after the head's target was waited for (M109) and ≥ min_extrude_temp; temperatures ≤ max_temp | actual temperature |
| Runtime mesh | extrusion only while the runtime mesh is cleared (compensation is embedded, never stacked) | — |
| Carriage limits / clearance | every carriage within its position limits and ≥ min clearance from its neighbour after every move | physical carriage width or missed steps |
| Parallel moves | only the non-ARRIVING, X-only branch of `QUADRO_PARALLEL_MOVE` is accepted and modelled as in the supplied firmware (largest-displacement carriage driven, others scaled → affine in one progress variable); limits and gaps checked at 21 points along the path; margins above min clearance reported; `ARRIVING`, `PLAN_ONLY`, `Y`, `Z`, or a parallel move during COPY are rejected | that carriages physically stayed where commanded |
| Travel clearance | non-extruding moves of every over-bed nozzle stay ≥ 0.5 mm above other copies' deposits (sampled every 1 mm); in-part travel never below own completed layers; descents never end inside material; parallel-move sweeps above deposits | nozzle/heater-block body geometry |
| Actuator limits | F ≤ max_velocity, ACCEL ≤ max_accel, parallel SPEED ≤ max_velocity, extrude-only ≤ max_extrude_only_distance, cross-section ≤ max_extrude_cross_section | motor torque margins |
| Prime lines | extrusion outside model blocks must be straight lines (≥ 20 mm); pairwise overlap of estimated bead widths is an error | adhesion |
| Copies | number of distinct placements = requested count, each complete on every layer exactly once (no double extrusion), footprints ≥ min gap | — |
| Final state | after the last extrusion: heaters and bed off, fans off, carriages **parked** at their endstop positions, T0 PRIMARY and T1–T3 INACTIVE, extruders on their own motion queues, G-code offset zero, no motion after `TURN_OFF_HEATERS`; the exact cleanup commands are listed | motors are left enabled (no `M84`), which the reference also does |
