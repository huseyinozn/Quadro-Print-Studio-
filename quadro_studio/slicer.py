"""Slicer G-code ingestion and classification.

The input is parsed as data. Every executable line is classified; anything
not explicitly accepted is a blocking diagnostic ("unknown motion is not
ignorable"). The result is a :class:`SliceModel` that separates

* the slicer's start-up section (``start_print`` parameters, initial retract),
* per-layer *gap* events (fan / bed / progress commands between objects),
* per-layer *approach* (travel + lowering + deretract that the slicer used to
  reach the object; replaced by the farm generator's own transfer),
* per-layer *model block* (every move from the first extrusion of the object
  to the end of the retraction/wipe that follows the object's stop marker;
  preserved verbatim except for translation and Z/E compensation),
* the end section (``M106 S0`` / ``end_print``; replaced by the generator).

ACCEPTED INPUT FEATURES (phase 1, see README):
  G0/G1 with X Y Z E F           motion (absolute XYZ, relative E)
  G90 G21 M83                    modal setup (required: absolute XYZ, relative E)
  G92 E0                         accepted (no-op in relative E)
  T0                             single tool selection before the first layer
  M104/M109 [T0] S               nozzle temperature (applied per printing head)
  M140/M190 S                    bed temperature
  M106 [P0] S / M107             part cooling fan (applied to the printing head(s))
  SET_VELOCITY_LIMIT             preserved verbatim (modal acceleration)
  SET_PRESSURE_ADVANCE ADVANCE=  applied to every printing extruder
  start_print / START_PRINT      parameters read; macro replaced by farm start-up
  end_print / END_PRINT          replaced by farm shutdown
  M73, SET_PRINT_STATS_INFO      progress metadata; regenerated
  TIMELAPSE_TAKE_PHOTO           dropped (would park the toolhead mid-print)
Everything else (arcs, G91, M82, firmware retraction, T1-T3, macros, ...)
is rejected with a diagnostic.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .diagnostics import Diagnostics, StageError

MAX_INPUT_BYTES = 256 * 1024 * 1024
WORD = re.compile(r"([A-Za-z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")
SUPPORTED_GENERATORS = ("CoPrintSlicer",)
RETRACT_TOL = 5e-5
UNSUPPORTED_LIMITS = {
    "M600": "filament change pauses one head; four synchronized heads cannot be paused per copy",
    "PAUSE": "a pause parks only the active toolhead; parked/COPY carriages are not handled",
    "RESUME": "resume restores a single-toolhead state",
    "M601": "pause: see PAUSE",
    "SET_RETRACTION": "firmware retraction parameters are not modelled (slicer retraction only)",
    "M207": "firmware retraction parameters are not modelled",
    "M220": "speed factor inside the print would change every copy's feedrates; not replicated",
    "M221": "flow factor inside the print would change E of every copy; not replicated",
    "SET_FAN_SPEED": "direct fan control would address one named fan, not the printing heads; use M106",
    "M900": "linear advance (Marlin) is not Klipper pressure advance",
    "SET_GCODE_OFFSET": "G-code offsets are owned by Farm Mode (tool offsets are applied in coordinates)",
    "BED_MESH_CALIBRATE": "probing during the print is not allowed",
    "BED_MESH_PROFILE": "runtime mesh would be stacked on the embedded compensation",
    "G28": "homing inside the print would move all carriages",
    "G29": "probing during the print is not allowed",
    "G4": "dwell not supported in phase 1 (timing differs between heads)",
    "M400": "wait-for-moves inside model blocks is not supported in phase 1",
    "SET_DUAL_CARRIAGE": "carriage modes are owned by Farm Mode",
    "ACTIVATE_EXTRUDER": "extruder selection is owned by Farm Mode",
    "SYNC_EXTRUDER_MOTION": "extruder synchronisation is owned by Farm Mode",
    "QUADRO_PARALLEL_MOVE": "carriage parking is owned by Farm Mode",
    "TIMELAPSE_RENDER": "timelapse is not supported",
}
UNSUPPORTED_PREFIX = (
    ("EXCLUDE_OBJECT", "object exclusion would cancel one copy's paths while the synchronized heads keep printing"),
    ("SET_PRESSURE_ADVANCE", "pressure advance must only set ADVANCE once for the single print extruder"),
)   # slicer prints E with 5 decimals; sums of wipe pieces drift by <= 2e-5


@dataclass
class Move:
    line: int
    x0: float
    y0: float
    z0: float
    x: float
    y: float
    z: float
    e: float
    f: float
    kind: str           # extrude | wipe | travel | zmove | retract | deretract
    width: float = 0.0
    ftype: str = ""
    accel: tuple = (None, None)   # modal (ACCEL, ACCEL_TO_DECEL) in force for this move

    @property
    def xy_len(self):
        return math.hypot(self.x - self.x0, self.y - self.y0)


@dataclass
class Cmd:
    line: int
    text: str
    kind: str           # accel | fan | pa | marker | comment


@dataclass
class Block:
    layer: int
    z: float                     # nominal layer z
    height: float                # nominal layer height
    items: list = field(default_factory=list)   # Move | Cmd in order
    deretract: float = 0.0       # E used by the slicer to prime before the block
    deretract_f: float = 0.0
    accel_at_start: str = ""     # SET_VELOCITY_LIMIT text in force at block start
    fan_at_start: float = 0.0    # 0..255
    start: tuple = (0.0, 0.0)
    end: tuple = (0.0, 0.0)
    retract_at_end: float = 0.0  # retract level after trailer
    first_line: int = 0
    trailer_index: int = -1      # index into items where the post-object retraction/wipe starts

    def moves(self):
        return [i for i in self.items if isinstance(i, Move)]


@dataclass
class SliceModel:
    path: str
    generator: str
    config: dict
    start_params: dict
    initial_retract: float
    blocks: list            # one per layer, index 0 = layer 1
    gap_events: dict        # layer -> list[Cmd] (applied before that layer's block)
    end_events: list
    pressure_advance: float
    nozzle_temp: float
    bed_temp: float
    bbox_extrude: tuple     # (xmin, ymin, xmax, ymax) over all layers
    bbox_motion: tuple      # over all block moves
    hull: list              # convex hull of deposited (extruded) points
    layer_bboxes: list
    stats: dict
    dropped: dict

    @property
    def layer_count(self):
        return len(self.blocks)


def _words(code):
    return {k.upper(): float(v) for k, v in WORD.findall(code)}


def convex_hull(points):
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _parse_config_block(lines, start, end):
    cfg = {}
    for l in lines[start:end]:
        if l.startswith("; ") and " = " in l:
            k, v = l[2:].split(" = ", 1)
            cfg.setdefault(k.strip(), v.strip())
    return cfg


def _first(v, typ=float):
    return typ(str(v).split(",")[0].strip().rstrip("%"))


def load_slice(path, diags: Diagnostics | None = None):
    diags = diags or Diagnostics()
    import os
    try:
        size = os.path.getsize(path)
    except OSError as e:
        diags.error("SLICE_MISSING", "cannot read sliced G-code: %s" % e)
        raise StageError(diags)
    if size > MAX_INPUT_BYTES:
        diags.error("SLICE_TOO_LARGE", "input is %d bytes (limit %d)" % (size, MAX_INPUT_BYTES))
        raise StageError(diags)
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        diags.error("SLICE_ENCODING", "input is not UTF-8 text G-code")
        raise StageError(diags)
    lines = text.split("\n")

    # --- header / dialect -------------------------------------------------------
    generator = ""
    for l in lines[:50]:
        m = re.match(r";\s*generated by\s+(\S+)\s*(\S*)", l)
        if m:
            generator = (m.group(1) + " " + m.group(2)).strip()
            break
    if not generator.startswith(SUPPORTED_GENERATORS):
        diags.error("SLICE_DIALECT", "unsupported or unknown slicer %r" % (generator or "?"),
                    "Phase 1 accepts G-code exported by Co Print Slicer (Klipper flavour).")
    try:
        ex_start = next(i for i, l in enumerate(lines) if l.strip() == "; EXECUTABLE_BLOCK_START")
        ex_end = next(i for i, l in enumerate(lines) if l.strip() == "; EXECUTABLE_BLOCK_END")
    except StopIteration:
        diags.error("SLICE_DIALECT", "missing EXECUTABLE_BLOCK markers; not a Co Print Slicer export")
        raise StageError(diags)
    try:
        cf_start = next(i for i, l in enumerate(lines) if l.strip() == "; CONFIG_BLOCK_START")
        cf_end = next(i for i, l in enumerate(lines) if l.strip() == "; CONFIG_BLOCK_END")
        cfg = _parse_config_block(lines, cf_start, cf_end)
    except StopIteration:
        diags.error("SLICE_CONFIG_MISSING", "missing slicer CONFIG_BLOCK; settings cannot be verified")
        raise StageError(diags)

    def need(key, ok, msg, hint=""):
        if key not in cfg:
            diags.error("SLICE_CONFIG_MISSING", "slicer setting %s missing" % key)
            return
        if not ok(cfg[key]):
            diags.error("SLICE_UNSUPPORTED_SETTING", "%s = %s: %s" % (key, cfg[key], msg), hint)

    need("gcode_flavor", lambda v: v == "klipper", "only Klipper flavour is supported")
    need("use_relative_e_distances", lambda v: v == "1", "relative E (M83) is required",
         "Enable 'Use relative E distances' and re-slice.")
    need("use_firmware_retraction", lambda v: v == "0", "firmware retraction (G10/G11) is not supported")
    need("enable_arc_fitting", lambda v: v == "0", "arc fitting (G2/G3) is not supported", "Disable arc fitting and re-slice.")
    need("extruder_offset", lambda v: all(p.strip() in ("0x0",) for p in v.split(",")), "slicer extruder offsets must be zero")
    need("spiral_mode", lambda v: v == "0", "spiral vase mode is not supported") if "spiral_mode" in cfg else None
    need("print_sequence", lambda v: v == "by layer", "only by-layer print sequence is supported") if "print_sequence" in cfg else None

    # --- executable section -------------------------------------------------
    x = y = z = 0.0
    f = 0.0
    abs_xyz = True
    rel_e = None
    level = 0.0                 # retract level (mm of filament withdrawn)
    width = 0.0
    ftype = ""
    accel = ""
    accel_state = (None, None)
    fan = 0.0
    tool_selected = False
    start_params = {}
    initial_retract = 0.0
    blocks = []
    gap_events = {}
    end_events = []
    pa = None
    dropped = {"M73": 0, "TIMELAPSE_TAKE_PHOTO": 0, "SET_PRINT_STATS_INFO": 0, "G92 E0": 0, "G21": 0}
    layer = 0
    layer_z = None
    mode = "preamble"           # preamble | gap | approach | block | trailer | end
    cur = None
    pending = None              # block being approached
    approach_de = 0.0
    approach_de_f = 0.0
    approach_z = None
    obj_markers = 0
    start_seen = end_seen = False
    nozzle_temp = None
    bed_temp = None
    events = []

    def err(code, msg, ln, hint=""):
        diags.error(code, msg, hint, line=ln + 1)

    def end_trailer():
        nonlocal mode
        if cur is not None:
            cur.retract_at_end = level
        mode = "gap"

    for i in range(ex_start + 1, ex_end):
        rawl = lines[i]
        s = rawl.strip()
        if not s:
            continue
        if s.startswith(";"):
            c = s[1:].strip()
            if s.startswith(";LAYER_CHANGE"):
                if mode in ("approach", "block"):
                    err("SLICE_STRUCTURE", "layer change inside an object block", i)
                layer += 1
                layer_z = None
                if mode == "preamble":
                    mode = "gap"
                gap_events.setdefault(layer, [])
                continue
            if s.startswith(";Z:"):
                layer_z = float(s[3:])
                continue
            if s.startswith(";WIDTH:"):
                width = float(s[7:])
                if mode == "block":
                    cur.items.append(Cmd(i + 1, s, "comment"))
                continue
            if s.startswith(";TYPE:"):
                ftype = s[6:]
                if mode == "block":
                    cur.items.append(Cmd(i + 1, s, "comment"))
                continue
            if c.startswith("printing object"):
                obj_markers += 1
                if mode not in ("gap", "trailer"):
                    err("SLICE_STRUCTURE", "object start marker in unexpected place (%s)" % mode, i)
                if mode == "trailer":
                    end_trailer()
                if layer == 0 or layer_z is None:
                    err("SLICE_STRUCTURE", "object outside a layer", i)
                    continue
                if len(blocks) != layer - 1:
                    err("SLICE_MULTI_OBJECT", "more than one object block in layer %d" % layer, i,
                        "Phase 1 supports one single model sliced once; slice exactly one copy.")
                    continue
                prev_z = blocks[-1].z if blocks else 0.0
                pending = Block(layer=layer, z=layer_z, height=round(layer_z - prev_z, 6), accel_at_start=accel)
                approach_de = 0.0
                approach_de_f = 0.0
                approach_z = z
                mode = "approach"
                continue
            if c.startswith("stop printing object"):
                if mode != "block":
                    err("SLICE_STRUCTURE", "object stop marker without object", i)
                    continue
                mode = "trailer"
                cur.trailer_index = len(cur.items)
                continue
            if s.startswith(";WIPE_START") or s.startswith(";WIPE_END"):
                if mode in ("block", "trailer") and cur is not None:
                    cur.items.append(Cmd(i + 1, s, "marker"))
                continue
            continue  # other comments carry no semantics

        code = s.split(";", 1)[0].strip()
        if not code:
            continue
        head = code.split()[0].upper()

        if head in ("G1", "G0"):
            w = _words(code[len(head):])
            bad = set(w) - set("XYZEF")
            if bad:
                err("SLICE_UNSUPPORTED_GCODE", "unsupported G1 parameter(s) %s" % ",".join(sorted(bad)), i)
                continue
            if not abs_xyz or rel_e is not True:
                err("SLICE_MODE", "motion before G90/M83 established absolute XYZ + relative E", i)
            nx, ny, nz = w.get("X", x), w.get("Y", y), w.get("Z", z)
            nf = w.get("F", f)
            e = w.get("E", 0.0)
            xy = (abs(nx - x) > 1e-9) or (abs(ny - y) > 1e-9)
            zc = abs(nz - z) > 1e-9
            if not xy and not zc and ("E" not in w or e == 0):
                f = nf
                x, y, z = nx, ny, nz
                if "E" in w:
                    dropped["G1 no-op (E0, no motion)"] = dropped.get("G1 no-op (E0, no motion)", 0) + 1
                continue  # pure feedrate change (modal) or zero-length E0 no-op
            if xy and e > 0:
                kind = "extrude"
            elif xy and e < 0:
                kind = "wipe"
            elif xy:
                kind = "travel"
            elif zc and e == 0:
                kind = "zmove"
            elif zc:
                kind = "zmove_e"
            elif e > 0:
                kind = "deretract"
            else:
                kind = "retract"
            mv = Move(i + 1, x, y, z, nx, ny, nz, e, nf, kind, width, ftype, accel_state)
            if kind == "zmove_e":
                err("SLICE_UNSUPPORTED_GCODE", "combined Z + E move without XY is not supported", i)
            # ---- state machine -------------------------------------------------
            if mode == "preamble":
                if kind == "retract":
                    initial_retract += -e
                elif kind == "deretract":
                    initial_retract -= e
                else:
                    err("SLICE_UNKNOWN_MOTION", "XY/Z motion before the first layer is not accepted (%s)" % code, i,
                        "Phase 1 replaces the slicer start-up; start G-code must not move the toolhead.")
            elif mode == "gap":
                if not blocks and kind in ("retract", "deretract"):
                    initial_retract += -e
                else:
                    err("SLICE_UNKNOWN_MOTION", "motion outside any object block: %s" % code, i,
                        "Unknown motion is not ignorable; this input is not supported.")
            elif mode == "approach":
                if kind == "extrude":
                    # block begins
                    cur = pending
                    pending = None
                    cur.deretract = approach_de
                    cur.deretract_f = approach_de_f
                    cur.fan_at_start = fan
                    cur.accel_at_start = accel     # modal state in force at the first extrusion
                    cur.start = (x, y)
                    cur.first_line = i + 1
                    if abs(z - cur.z) > 1e-6:
                        err("SLICE_STRUCTURE", "first extrusion of layer %d at Z=%.4f, layer Z is %.4f" % (cur.layer, z, cur.z), i)
                    if abs(level - approach_de) > 1e-6 and False:
                        pass
                    blocks.append(cur)
                    mode = "block"
                    cur.items.append(mv)
                elif kind in ("travel", "zmove"):
                    pass
                elif kind == "deretract":
                    approach_de += e
                    approach_de_f = nf
                elif kind == "retract":
                    approach_de += e
                else:
                    err("SLICE_UNKNOWN_MOTION", "unexpected %s move while approaching object: %s" % (kind, code), i)
            elif mode == "block":
                if mv.z < cur.z - 1e-6:
                    err("SLICE_GEOMETRY", "move below layer Z inside layer %d (Z=%.4f)" % (cur.layer, mv.z), i)
                cur.items.append(mv)
            elif mode == "trailer":
                if kind in ("wipe", "retract"):
                    cur.items.append(mv)
                else:
                    end_trailer()
                    err("SLICE_UNKNOWN_MOTION", "motion after object stop marker that is not a retraction/wipe: %s" % code, i)
            elif mode == "end":
                err("SLICE_UNKNOWN_MOTION", "motion after end_print: %s" % code, i)
            # retract level bookkeeping
            if kind == "extrude":
                if abs(level) > RETRACT_TOL:
                    err("SLICE_RETRACTION", "extrusion while filament retracted by %.6f mm" % level, i,
                        "Retract/deretract amounts must balance (no 'restart extra').")
                level = 0.0
            elif kind in ("wipe", "retract"):
                level += -e
            elif kind == "deretract":
                level -= e
                if abs(level) <= RETRACT_TOL:
                    level = 0.0
                if level < -RETRACT_TOL:
                    err("SLICE_RETRACTION", "deretraction exceeds previous retraction by %.4f mm" % (-level), i)
            x, y, z, f = nx, ny, nz, nf
            continue

        # ---- non-motion commands ----------------------------------------------
        target = (cur.items if mode == "block" else
                  (gap_events.setdefault(layer, []) if mode in ("gap", "approach") else
                   (events if mode == "preamble" else end_events)))
        if head == "G90":
            abs_xyz = True
        elif head == "G91":
            abs_xyz = False
            err("SLICE_UNSUPPORTED_GCODE", "relative positioning (G91) is not supported", i)
        elif head == "M83":
            rel_e = True
        elif head == "M82":
            rel_e = False
            err("SLICE_UNSUPPORTED_GCODE", "absolute extrusion (M82) is not supported", i, "Enable relative E in the slicer.")
        elif head == "G21":
            dropped["G21"] += 1
        elif head == "G92":
            w = _words(code[3:])
            if set(w) == {"E"} and w["E"] == 0:
                dropped["G92 E0"] += 1
            else:
                err("SLICE_UNSUPPORTED_GCODE", "G92 with axes other than E0 is not supported: %s" % code, i)
        elif head in ("G2", "G3"):
            err("SLICE_UNSUPPORTED_GCODE", "arc moves (%s) are not supported" % head, i, "Disable arc fitting and re-slice.")
        elif head in ("G10", "G11"):
            err("SLICE_UNSUPPORTED_GCODE", "firmware retraction is not supported", i)
        elif re.fullmatch(r"T\d+", head):
            if head != "T0" or mode != "preamble" or tool_selected:
                err("SLICE_UNSUPPORTED_TOOL", "tool change %s is not supported" % head, i,
                    "Phase 1 supports one single-colour model printed with T0 in the slice.")
            tool_selected = True
        elif head == "M73":
            dropped["M73"] += 1
        elif head == "SET_PRINT_STATS_INFO":
            dropped["SET_PRINT_STATS_INFO"] += 1
        elif head == "TIMELAPSE_TAKE_PHOTO":
            dropped["TIMELAPSE_TAKE_PHOTO"] += 1
        elif head == "SET_VELOCITY_LIMIT":
            params = dict(re.findall(r"([A-Z_]+)=([-+0-9.]+)", code.upper()))
            if set(params) - {"ACCEL", "ACCEL_TO_DECEL", "VELOCITY", "SQUARE_CORNER_VELOCITY", "MINIMUM_CRUISE_RATIO"}:
                err("SLICE_UNSUPPORTED_GCODE", "unsupported SET_VELOCITY_LIMIT parameters: %s" % code, i)
            accel = code
            accel_state = (float(params["ACCEL"]) if "ACCEL" in params else accel_state[0],
                           float(params["ACCEL_TO_DECEL"]) if "ACCEL_TO_DECEL" in params else accel_state[1])
            if mode == "block":
                cur.items.append(Cmd(i + 1, code, "accel"))
        elif head == "SET_PRESSURE_ADVANCE":
            params = dict(re.findall(r"([A-Z_]+)=([^\s]+)", code.upper()))
            if set(params) != {"ADVANCE"}:
                err("SLICE_UNSUPPORTED_GCODE", "SET_PRESSURE_ADVANCE must only set ADVANCE: %s" % code, i)
            else:
                v = float(params["ADVANCE"])
                if pa is not None and mode != "preamble" and abs(v - pa) > 1e-12:
                    err("SLICE_UNSUPPORTED_FEATURE", "pressure advance changes during the print", i)
                pa = v
        elif head in ("M106", "M107"):
            w = _words(code[4:])
            if head == "M107":
                val = 0.0
            else:
                if "P" in w and w["P"] != 0:
                    err("SLICE_UNSUPPORTED_GCODE", "auxiliary fan M106 P%d not supported" % w["P"], i)
                val = max(0.0, min(255.0, w.get("S", 255.0)))
            fan = val
            cmd = Cmd(i + 1, "FAN %.4f" % val, "fan")
            if mode == "block":
                cur.items.append(cmd)
            elif mode in ("gap", "approach", "trailer"):
                gap_events.setdefault(layer + (1 if mode in ("gap", "trailer") and blocks and blocks[-1].layer == layer else 0), []).append(cmd)
            elif mode == "end":
                end_events.append(cmd)
            else:
                events.append(cmd)
        elif head in ("M140", "M190"):
            w = _words(code[4:])
            v = w.get("S")
            if v is None:
                err("SLICE_UNSUPPORTED_GCODE", "%s without S" % head, i)
            elif bed_temp is None:
                bed_temp = v
            elif abs(v - bed_temp) > 1e-9:
                err("SLICE_UNSUPPORTED_FEATURE", "bed temperature changes during the print (%.1f -> %.1f)" % (bed_temp, v), i,
                    "Phase 1 keeps the bed at one temperature for all heads.")
        elif head in ("M104", "M109"):
            w = _words(code[4:])
            if "T" in w and w["T"] != 0:
                err("SLICE_UNSUPPORTED_TOOL", "temperature command for T%d" % w["T"], i)
            v = w.get("S")
            if v is None:
                err("SLICE_UNSUPPORTED_GCODE", "%s without S" % head, i)
            elif nozzle_temp is None:
                nozzle_temp = v
            elif abs(v - nozzle_temp) > 1e-9:
                err("SLICE_UNSUPPORTED_FEATURE", "nozzle temperature changes during the print (%.1f -> %.1f)" % (nozzle_temp, v), i)
        elif head in ("START_PRINT",) or code.split()[0] in ("start_print", "START_PRINT"):
            if start_seen or mode != "preamble":
                err("SLICE_STRUCTURE", "start_print must appear once before the first layer", i)
            start_seen = True
            for k, v in re.findall(r"([A-Za-z0-9_]+)=('[^']*'|\"[^\"]*\"|\S+)", code):
                start_params[k.upper()] = v.strip("'\"")
        elif code.split()[0] in ("end_print", "END_PRINT"):
            if end_seen:
                err("SLICE_STRUCTURE", "end_print appears twice", i)
            if mode == "trailer":
                end_trailer()
            end_seen = True
            mode = "end"
        else:
            name = code.split()[0].upper()
            lim = UNSUPPORTED_LIMITS.get(name)
            if lim is None:
                for pref, txt in UNSUPPORTED_PREFIX:
                    if name.startswith(pref):
                        lim = txt
                        break
            if lim is None:
                lim = ("macro or command with unknown semantics; Farm Mode cannot tell whether it moves the toolhead, "
                       "extrudes or changes machine state, so it cannot be replicated safely on four heads")
            err("SLICE_UNSUPPORTED_GCODE", "unsupported command %s: %s" % (name, lim), i,
                "Remove it in the slicer (custom G-code) and re-slice; accepted commands are listed in README.md.")

    if mode == "trailer":
        end_trailer()
    if not start_seen:
        diags.error("SLICE_STRUCTURE", "no start_print call found; cannot read print temperatures")
    if not end_seen:
        diags.error("SLICE_STRUCTURE", "no end_print found; the slice looks truncated")
    if not blocks:
        diags.error("SLICE_EMPTY", "no printable layers found")
    if blocks and len(blocks) != layer:
        diags.error("SLICE_STRUCTURE", "%d layers but %d object blocks" % (layer, len(blocks)))
    if obj_markers != len(blocks):
        diags.error("SLICE_MULTI_OBJECT", "%d object markers for %d layers" % (obj_markers, len(blocks)))

    # start_print parameters
    if start_seen:
        if start_params.get("EXTRUDER", "0") != "0":
            diags.error("SLICE_UNSUPPORTED_TOOL", "slice starts with EXTRUDER=%s; phase 1 needs T0" % start_params.get("EXTRUDER"))
        for t in (1, 2, 3):
            if start_params.get("EXT%d" % t, "false").lower() == "true":
                diags.error("SLICE_UNSUPPORTED_TOOL", "slice uses extruder %d; only single-colour (T0) slices are supported" % t,
                            "Dual Color Copy Mode is a later milestone; re-slice with one filament.")
        try:
            t0 = float(start_params.get("EXT0_TEMP", start_params.get("EXTRUDER_TEMP")))
            nozzle_temp = nozzle_temp if nozzle_temp is not None else t0
            if abs(t0 - nozzle_temp) > 1e-9:
                diags.error("SLICE_UNSUPPORTED_FEATURE", "first-layer and later nozzle temperatures differ")
            bt = float(start_params["BED_TEMP"])
            if bed_temp is not None and abs(bt - bed_temp) > 1e-9:
                diags.error("SLICE_UNSUPPORTED_FEATURE", "first-layer and later bed temperatures differ")
            bed_temp = bt
        except (TypeError, KeyError, ValueError):
            diags.error("SLICE_START_PARAMS", "start_print lacks EXTRUDER_TEMP/BED_TEMP")
    if pa is None:
        diags.warning("SLICE_NO_PA", "slice sets no pressure advance; printer defaults will apply")

    if not diags.ok():
        raise StageError(diags)

    # check approach deretract balances previous retraction
    lvl = initial_retract
    for b in blocks:
        if abs(b.deretract - lvl) > RETRACT_TOL:
            diags.error("SLICE_RETRACTION", "layer %d: approach primes %.4f mm but %.4f mm were retracted" % (b.layer, b.deretract, lvl))
        lvl = b.retract_at_end
    for b in blocks:
        mv = b.moves()
        b.end = (mv[-1].x, mv[-1].y)
    if not diags.ok():
        raise StageError(diags)

    # footprint
    ex_pts = []
    xs, ys = [], []
    layer_bb = []
    n_moves = n_ext = 0
    total_e = 0.0
    for b in blocks:
        lx, ly = [], []
        for m in b.moves():
            n_moves += 1
            xs += [m.x0, m.x]
            ys += [m.y0, m.y]
            if m.kind == "extrude":
                n_ext += 1
                total_e += m.e
                ex_pts.append((round(m.x0, 3), round(m.y0, 3)))
                ex_pts.append((round(m.x, 3), round(m.y, 3)))
                lx += [m.x0, m.x]
                ly += [m.y0, m.y]
                if m.width <= 0:
                    diags.error("SLICE_WIDTH", "extrusion without ;WIDTH annotation", line=m.line)
        layer_bb.append((min(lx), min(ly), max(lx), max(ly)) if lx else None)
    exs = [p[0] for p in ex_pts]
    eys = [p[1] for p in ex_pts]
    hull = convex_hull(ex_pts)
    cfgp = {
        "retraction_length": _first(cfg.get("retraction_length", "0")),
        "retraction_speed": _first(cfg.get("retraction_speed", "30")),
        "retract_length_toolchange": _first(cfg.get("retract_length_toolchange", "0")),
        "travel_speed": _first(cfg.get("travel_speed", "150")),
        "travel_acceleration": _first(cfg.get("travel_acceleration", "0")),
        "initial_layer_travel_acceleration": _first(cfg.get("initial_layer_travel_acceleration", "0")),
        "default_acceleration": _first(cfg.get("default_acceleration", "0")),
        "nozzle_diameter": _first(cfg.get("nozzle_diameter", "0.4")),
        "filament_diameter": _first(cfg.get("filament_diameter", "1.75")),
        "layer_height": _first(cfg.get("layer_height", "0.2")),
        "initial_layer_print_height": _first(cfg.get("initial_layer_print_height", "0.2")),
        "z_hop": _first(cfg.get("z_hop", "0")),
        "printable_area": cfg.get("printable_area", ""),
        "deretraction_speed": _first(cfg.get("deretraction_speed", "0")),
    }
    model = SliceModel(path=path, generator=generator, config=cfgp, start_params=start_params,
                       initial_retract=initial_retract, blocks=blocks, gap_events=gap_events, end_events=end_events,
                       pressure_advance=pa if pa is not None else 0.0, nozzle_temp=nozzle_temp, bed_temp=bed_temp,
                       bbox_extrude=(min(exs), min(eys), max(exs), max(eys)),
                       bbox_motion=(min(xs), min(ys), max(xs), max(ys)), hull=hull, layer_bboxes=layer_bb,
                       stats={"layers": len(blocks), "block_moves": n_moves, "extrusions": n_ext,
                              "filament_mm": round(total_e, 3), "max_z": max(m.z for b in blocks for m in b.moves())},
                       dropped=dropped)
    if events:
        for c in events:
            if c.kind == "fan" and float(c.text.split()[1]) != 0:
                diags.warning("SLICE_PREAMBLE_FAN", "fan command before first layer is applied from layer 1")
    if not diags.ok():
        raise StageError(diags)
    return model, diags
