"""Plain-English explanations of diagnostics and verification results."""

PLAIN = {
    "PROFILE_MESH_MISSING": "The printer profile has no saved bed mesh. Run the bed mesh calibration on the printer, save it, and export the profile again.",
    "PROFILE_MESH_AMBIGUOUS": "The printer profile contains several saved bed meshes, so it is not clear which one is current. Export a profile with only the current mesh.",
    "PROFILE_OFFSETS_MISSING": "The printer profile has no tool offset file (bed_pin_offsets.json). Run the tool offset calibration and export the profile again.",
    "PROFILE_OFFSETS_INVALID": "The tool offset file in the profile is incomplete or damaged.",
    "PROFILE_OFFSETS_IMPLAUSIBLE": "The tool offsets in the profile are outside a plausible range. Re-run the tool offset calibration.",
    "PROFILE_UNSAFE_PATH": "The profile ZIP contains unsafe file paths and was not opened.",
    "PROFILE_ZIP_BOMB": "The profile ZIP expands to an unexpected size and was not opened.",
    "PROFILE_TOO_LARGE": "The profile ZIP is too large.",
    "PROFILE_UNREADABLE": "The profile file is not a readable ZIP archive.",
    "PROFILE_PRINTER_CFG": "The profile ZIP must contain exactly one printer.cfg.",
    "PROFILE_SECTION_MISSING": "The profile does not describe a four-head Quadro (carriages, extruders or the parallel-carriage module are missing).",
    "PROFILE_INCLUDE_MISSING": "printer.cfg includes a file that is missing from the profile ZIP.",
    "PROFILE_MACRO_MISSING": "A required printer macro is missing from the profile.",
    "SLICE_DIALECT": "This G-code was not exported by Co Print Slicer (Klipper flavour).",
    "SLICE_CONFIG_MISSING": "The G-code has no slicer settings block, so its settings cannot be checked.",
    "SLICE_UNSUPPORTED_SETTING": "A slicer setting is not supported by Farm Mode.",
    "SLICE_UNSUPPORTED_GCODE": "The G-code contains a command Farm Mode cannot reproduce on four heads.",
    "SLICE_UNSUPPORTED_TOOL": "The G-code uses more than one extruder. Farm Mode needs a single-colour model sliced for T0.",
    "SLICE_UNSUPPORTED_FEATURE": "The G-code changes temperatures during the print, which Farm Mode does not support yet.",
    "SLICE_UNKNOWN_MOTION": "The G-code contains movement outside the model that Farm Mode cannot classify.",
    "SLICE_MULTI_OBJECT": "The G-code contains more than one object. Slice exactly one copy of one model.",
    "SLICE_STRUCTURE": "The G-code structure is incomplete or unexpected (it may be truncated).",
    "SLICE_RETRACTION": "The G-code's retraction amounts do not balance.",
    "SLICE_ENCODING": "The model file is not a text G-code file.",
    "SLICE_EMPTY": "The G-code contains no printable layers.",
    "PLAN_COUNT": "Farm Mode needs a multiple of 4 copies (one copy per head per group); Copy Mode prints exactly 2.",
    "PLAN_PARKED_HEADS_CLEAR": "The printing heads would come too close to the parked heads.",
    "PLAN_DOES_NOT_FIT": "That many copies of this model do not fit on the measured bed area.",
    "PLAN_BED_BOUNDS": "Some copies would extend beyond the printable area.",
    "PLAN_MESH_COVERAGE": "Some copies would lie outside the measured bed mesh.",
    "PLAN_COPY_GAP": "Copies would be closer together than the minimum gap.",
    "PLAN_COPY_CLEARANCE": "The four heads would be too close to each other while printing together.",
    "PLAN_PURGE_LANES": "There is not enough room at the front for the prime lines.",
    "PLAN_Y_LIMITS": "The layout exceeds the printer's Y travel.",
    "PLAN_FOUNDATION_THICKNESS": "The bed and head heights differ too much to even out in the first layers.",
    "PLAN_NOZZLE": "The model was sliced for a different nozzle size than the printer has.",
    "PLAN_FILAMENT": "The model was sliced for a different filament diameter.",
    "PLAN_TEMP": "The model's temperature is outside the printer's limits.",
    "DUAL_NOT_COPYABLE": "The two models are not coloured the same way. In Dual Color Copy Mode the right model copies the left model's moves, so both models must have identical colour regions on every layer.",
    "DUAL_TOOLS": "This file is not a four-tool, two-colour slice with two models (left: T0 and T2, right: T1 and T3).",
    "DUAL_MISSING_REGION": "One of the four tools has nothing to print on some layers; every head needs its colour region on every layer.",
    "DUAL_LAYERS": "The four tools of this slice do not share the same layers.",
    "PLAN_PAIR_Z_CONSISTENCY": "Height (Z): where one colour is printed on the other, the layer is further from its nominal height than allowed (each pair prints both models at one shared height).",
    "PLAN_PAIR_Y_REGISTRATION": "Colour alignment (Y): the two colours of a model are shifted against each other by more than allowed (each pair prints both models at one shared Y).",
    "PLAN_INTO_MATERIAL": "A nozzle would print or descend lower than the other colour's material beside or below it (layer, head and position are listed in the layout).",
    "PLAN_COLOUR_STACK_ROOM": "Room for the filament: where one colour is printed on the other, there is not enough room under the nozzle on both models, and the pair heights and the shared filament amount cannot be set to fit both models at once.",
    "PLAN_TRAVEL_DRAG": "A travel or wipe would pass through the other colour's material.",
    "PLAN_TOWER_CLEARANCE": "The wipe towers would be too close to the models.",
    "PLAN_PAIR_PARKS": "A printing pair would come too close to the parked heads.",
    "PLAN_PAIR_A_SPACING": "T0 and T1 would be too close together while printing together.",
    "PLAN_PAIR_B_SPACING": "T2 and T3 would be too close together while printing together.",
}

VERIFY_PLAIN = {
    "COPY_PHASE": "The heads do not switch to printing together exactly after the first layers, or the wrong heads print together.",
    "PARKED_HEAD_MOVED": "A head that should stay parked is moved during the print.",
    "PARKED_HEAD_ACTIVE": "A head that should stay parked is made active or synchronised.",
    "FOUNDATION_ORDER": "The heads do not print their first layers one after another in the expected order.",
    "VISIT_MISMATCH": "Some model moves of a copy were missing or changed.",
    "COPY_COUNT": "The number of complete copies is not what was requested.",
    "COPY_INCOMPLETE": "A copy is missing a layer or a layer was printed twice.",
    "COPY_OVERLAP": "Two copies are too close together.",
    "ACCELERATION": "An acceleration setting differs from the sliced model.",
    "PRESSURE_ADVANCE": "A head's pressure advance differs from the sliced model.",
    "FIRST_LAYER_HEIGHT": "The first layer is not at the correct height above the measured bed.",
    "COPY_THICKNESS": "A layer printed by all four heads is not the correct thickness.",
    "FOUNDATION_THICKNESS": "A first-layer compensation step is outside the allowed thickness.",
    "FOUNDATION_FLOW": "The filament amount in the first layers does not match the layer thickness.",
    "COMMON_Z": "The four heads do not end the first layers at the same height.",
    "TRANSFER_CLEARANCE": "A travel move passes too close above printed material.",
    "INPART_SINK": "A travel move goes below already printed layers.",
    "DESCENT_INTO_MATERIAL": "A head lowers into printed material.",
    "CARRIAGE_CLEARANCE": "Two carriages come closer than the minimum clearance.",
    "CARRIAGE_LIMIT": "A carriage moves outside its travel range.",
    "QPM_CLEARANCE": "A parking move brings two carriages too close.",
    "QPM_LIMIT": "A parking move goes outside a carriage's travel range.",
    "QPM_UNSUPPORTED_PROFILE": "A parking move uses a motion profile that cannot be verified.",
    "QPM_SWEEP_CLEARANCE": "A parking move passes too close above printed material.",
    "RETRACT_STATE": "A head starts printing while its filament is still retracted.",
    "OVER_PRIME": "A head pushes out more filament than it retracted.",
    "COLD_EXTRUDE": "A head extrudes before reaching its printing temperature.",
    "MESH_STACKED": "The printer's runtime bed mesh would be applied on top of the built-in compensation.",
    "FINAL_STATE": "The machine is not left in a safe state at the end (heaters, fans, carriages or extruders).",
    "PURGE_OVERLAP": "Prime lines overlap each other.",
    "PURGE_SHAPE": "There is extrusion outside the model that is not a straight prime line.",
    "TEMP_LIMIT": "A temperature exceeds the printer's limit.",
    "ACCEL_LIMIT": "An acceleration exceeds the printer's limit.",
    "VELOCITY_LIMIT": "A speed exceeds the printer's limit.",
    "OUT_UNKNOWN_COMMAND": "The file contains a command outside the verified set.",
    "EXTRUDE_CROSS_SECTION": "An extrusion exceeds the printer's extrusion limit.",
    "EXTRUDE_ONLY_DISTANCE": "A retraction or prime exceeds the printer's limit.",
    "OUT_PARKED_EXTRUDE": "A parked head's extruder pushes filament.",
    "DUAL_REGION": "A head does not print exactly its own colour region on its own model on every layer.",
    "DUAL_REGISTRATION": "The two colours of a model do not line up.",
    "DUAL_PAIR": "The wrong heads print together (expected T0 + T1, then T2 + T3).",
    "DUAL_TOWER": "A wipe tower is missing, misplaced or printed at the wrong speed.",
    "DUAL_PRIME": "A head's prime line is missing or not directly before its first print.",
    "TOWER_THICKNESS": "A wipe tower layer has the wrong thickness.",
    "TOWER_BASE": "A wipe tower layer has nothing below it.",
    "DUAL_Z_DEVIATION": "Where one colour is printed on the other, the layer is further from its nominal height than allowed.",
    "DUAL_STACK_ROOM": "Where one colour is printed on the other there is not enough room under the nozzle for the filament.",
    "DUAL_INTO_MATERIAL": "A nozzle prints or descends lower than the other colour's material.",
    "DUAL_TRAVEL_DRAG": "A travel or wipe passes through the other colour's material.",
    "CARRIAGE_DETOUR": "A waiting head is moved towards the printing heads and back (unnecessary carriage travel).",
}


def explain(diags):
    out = []
    seen = set()
    for d in diags.errors:
        base = PLAIN.get(d.code) or PLAIN.get(d.code.split("_T")[0]) or "This input cannot be used."
        key = (base, d.message)
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(code=d.code, text=base, detail=d.message, hint=d.hint, where=d.where))
    return out[:12]


def warnings(diags):
    out = []
    for d in diags.warnings:
        if d.code == "MESH_DEVIATING_POINT":
            text = "A measured bed mesh point differs clearly from its neighbours. It is used exactly as measured; the copies that depend on it are marked."
        elif d.code == "PLAN_DEVIATING_POINT_AFFECTS_COPIES":
            text = "Some copies depend on that mesh point. Check the bed there before printing."
        elif d.code == "PROFILE_FRESHNESS_UNVERIFIED":
            text = "Make sure the profile contains your current bed mesh and tool offsets."
        elif d.code == "PLAN_PURGE_UNMEASURED":
            text = "The prime lines at the front edge are outside the measured mesh area."
        else:
            text = d.message
        out.append(dict(code=d.code, text=text, detail=d.message))
    return out


def headline(diags):
    e = diags.errors
    if not e:
        return "OK"
    return (PLAIN.get(e[0].code) or PLAIN.get(e[0].code.split("_T")[0]) or e[0].message)


def verify_summary(res, exit_code):
    if not res:
        return dict(passed=False, problems=[dict(code="VERIFY_ERROR", text="The verification could not be completed.")])
    probs = []
    for code, n in sorted(res.get("issue_counts", {}).items()):
        ex = next((i for i in res.get("issues", []) if i["code"] == code), None)
        probs.append(dict(code=code, count=n, text=VERIFY_PLAIN.get(code, "Check %s failed." % code),
                          detail=ex["message"] if ex else ""))
    st = res.get("stats", {})
    fs = res.get("final_state", {})
    return dict(
        passed=bool(res.get("passed")) and exit_code == 0,
        copies_complete=res.get("copies_complete"), copies_found=res.get("copies_found"),
        min_gap_mm=res.get("min_gap_between_copies"),
        first_layer_max_dev_mm=st.get("first_layer_max_dev"), min_transfer_clearance_mm=st.get("min_transfer_clear"),
        min_carriage_gap_mm=st.get("min_carriage_gap"), min_sync_head_gap_mm=st.get("min_synchronized_head_gap"), final_parked=fs.get("carriages_parked"),
        heaters_off=fs.get("heaters_off"), problems=probs,
        layers_per_copy=sorted((res.get("layers_per_copy") or {}).values()),
        synchronized_from_layer=res.get("synchronized_from_layer"),
        parked_heads=(res.get("parked_heads") or {}).get("heads"),
        parked_heads_stayed=(res.get("parked_heads") or {}).get("stayed_parked"),
        mode=res.get("mode"), region_layers_ok=res.get("dual_region_layers_ok"),
        registration_mm=res.get("dual_registration_mm"), min_gap_models_mm=res.get("min_gap_between_models_mm"),
        tower_gap_mm=res.get("tower_min_gap_to_models_mm"),
        z_deviation_mm=(res.get("dual_z_deviation") or {}).get("max_mm"),
        into_material_mm=(res.get("dual_into_material") or {}).get("deepest_seen_mm"),
        travel_drag_mm=(res.get("dual_travel_drag") or {}).get("deepest_seen_mm"),
        limits=res.get("dual_limits"),
        stack_room=res.get("dual_stack_room"), stack_room_min=res.get("dual_stack_room_min_mm"))
