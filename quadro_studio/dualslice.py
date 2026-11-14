"""Two-colour (4-tool) Co Print Slicer input for Dual Color Copy Mode.

The slice contains two models side by side. Tool mapping (fixed for this mode):
    left model:  colour 1 = T0, colour 2 = T2
    right model: colour 1 = T1, colour 2 = T3
In the output T0+T1 print their colour-1 regions together (T1 in COPY), then
T2+T3 print the colour-2 regions together. That is only possible when every
layer's T1 region is exactly the T0 region translated by one constant vector,
and the T3 region is the T2 region translated by the SAME vector. This module
proves that, move by move, or refuses the input.

How the input is read
* Each tool's model sections (between the slicer's ``printing object`` /
  ``stop printing object`` markers, one per tool and layer) are extracted
  verbatim and re-assembled into a single-tool stream per tool, which is then
  parsed by the regular, strict single-model reader (``slicer.load_slice``):
  same accepted commands, retraction balance and unknown-motion rules.
* Outside the model sections only the slicer's own tool-change machinery is
  accepted and replaced by Farm/Copy-style transfers: ``CP TOOLCHANGE`` blocks,
  ``Prime tower`` segments, tool selects, standby/preheat temperatures (ooze
  prevention), dwell ``G4``, ``M220 S100``, ``G92 E0``, ``RESPOND`` and the
  pressure-advance re-set with the same value. Any motion outside these is
  refused ("unknown motion").
* Retraction state between sections is restored with an explicit retract of the
  amount the next section primes (the slicer did this inside its tool change).
"""
from __future__ import annotations

import math
import os
import re
import tempfile

from .diagnostics import Diagnostics, StageError
from .slicer import load_slice, Move

PAIRS = {"A": (0, 1), "B": (2, 3)}          # (left/primary, right/copy)
LEFT, RIGHT = (0, 2), (1, 3)
W = re.compile(r"([A-Z])([-+]?(?:\d+\.?\d*|\.\d+))")


class DualSlice:
    def __init__(self, path, models, colours, preheat_time, standby_delta, translation, config, tool_order, stats):
        self.path = path
        self.models = models                  # tool -> SliceModel (single-tool view of that tool's regions)
        self.colours = colours                # tool -> "#RRGGBB"
        self.preheat_time = preheat_time
        self.standby_delta = standby_delta
        self.translation = translation        # (dx, dy): right model = left model + this, in slice coordinates
        self.config = config
        self.tool_order = tool_order
        self.stats = stats

    @property
    def layer_count(self):
        return self.models[0].layer_count


def _cfg(lines):
    cfg = {}
    try:
        a = next(i for i, l in enumerate(lines) if l.strip() == "; CONFIG_BLOCK_START")
        b = next(i for i, l in enumerate(lines) if l.strip() == "; CONFIG_BLOCK_END")
    except StopIteration:
        return cfg, (0, 0)
    for l in lines[a:b]:
        if l.startswith("; ") and " = " in l:
            k, v = l[2:].split(" = ", 1)
            cfg.setdefault(k.strip(), v.strip())
    return cfg, (a, b)


def _level_after(section, start_level):
    """Retract level after replaying a section's E moves (same rules as the reader)."""
    lvl = start_level
    x = y = None
    for l in section:
        s = l.split(";", 1)[0].strip()
        if not s.startswith(("G1", "G0")):
            continue
        w = {k: float(v) for k, v in W.findall(s[2:])}
        nx, ny = w.get("X", x), w.get("Y", y)
        xy = (x is not None and nx is not None and (nx != x or ny != y)) or (x is None and ("X" in w or "Y" in w))
        e = w.get("E", 0.0)
        if "E" in w and e != 0:
            if e > 0 and xy:
                lvl = 0.0
            else:
                lvl -= e
        x, y = nx, ny
    return lvl


def split_tools(path, diags):
    with open(path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        diags.error("SLICE_ENCODING", "input is not UTF-8 text G-code")
        raise StageError(diags)
    lines = text.split("\n")
    cfg, (ca, cb) = _cfg(lines)
    try:
        ex0 = next(i for i, l in enumerate(lines) if l.strip() == "; EXECUTABLE_BLOCK_START")
        ex1 = next(i for i, l in enumerate(lines) if l.strip() == "; EXECUTABLE_BLOCK_END")
    except StopIteration:
        diags.error("SLICE_DIALECT", "missing EXECUTABLE_BLOCK markers; not a Co Print Slicer export")
        raise StageError(diags)
    header = lines[:ex0 + 1]
    config_tail = lines[ex1:]

    tool = None
    layer = 0
    zs, hs = {}, {}
    sections = {t: {} for t in range(4)}     # tool -> layer -> [lines]
    fans = {t: {} for t in range(4)}         # tool -> layer -> [fan lines before its section]
    pending_fans = []
    order = {}
    start = None
    pa = None
    in_section = None
    in_toolchange = False
    in_tower = False
    used = set()
    errs = 0

    def err(code, msg, ln, hint=""):
        nonlocal errs
        errs += 1
        if errs <= 25:
            diags.error(code, msg, hint, line=ln + 1)

    for i in range(ex0 + 1, ex1):
        l = lines[i]
        s = l.strip()
        code = s.split(";", 1)[0].strip()
        if in_section is not None:
            if s.startswith("; stop printing object"):
                in_section = None
                continue
            if s.startswith(";LAYER_CHANGE"):
                err("SLICE_STRUCTURE", "layer change inside an object section", i)
            if re.match(r"M104\s.*\bT\d", code) and "preheat" in s:
                continue                                       # slicer ooze-prevention preheat: replaced
            if code.startswith("T") and re.fullmatch(r"T\d+.*", code):
                err("SLICE_STRUCTURE", "tool change inside an object section", i)
            sections[in_section][layer].append(l)
            continue
        if s.startswith(";LAYER_CHANGE"):
            layer += 1
            order[layer] = []
            continue
        if s.startswith(";Z:") and layer:
            zs.setdefault(layer, float(s[3:]))
            continue
        if s.startswith(";HEIGHT:") and layer and layer not in hs:
            hs[layer] = float(s[8:])
            continue
        if s.startswith("; printing object"):
            if tool is None or layer == 0:
                err("SLICE_STRUCTURE", "object outside a layer or before a tool is selected", i)
                continue
            if layer in sections[tool]:
                err("SLICE_MULTI_OBJECT", "T%d has two object sections on layer %d" % (tool, layer), i)
                continue
            sections[tool][layer] = []
            fans[tool][layer] = list(pending_fans)
            pending_fans = []
            order[layer].append(tool)
            in_section = tool
            used.add(tool)
            continue
        if "CP TOOLCHANGE START" in s:
            in_toolchange = True
            continue
        if "CP TOOLCHANGE END" in s:
            in_toolchange = False
            continue
        if s.startswith(";TYPE:"):
            in_tower = s[6:].strip() == "Prime tower"
            continue
        if not code:
            continue
        head = code.split()[0]
        if head.lower() == "start_print":
            start = code
            continue
        m = re.fullmatch(r"T(\d)(\s.*)?", code)
        if m:
            tool = int(m.group(1))
            if tool > 3:
                err("SLICE_UNSUPPORTED_TOOL", "tool T%d does not exist on a Quadro" % tool, i)
            continue
        if head in ("G1", "G0"):
            if layer == 0 and tool is None:
                w = {k: float(v) for k, v in W.findall(code[2:])}
                if set(w) <= {"E", "F"}:
                    continue
            if in_toolchange or in_tower:
                continue                                       # slicer tower / tool-change motion: replaced
            w = {k: float(v) for k, v in W.findall(code[2:])}
            if set(w) <= {"E", "F", "Z"}:
                continue                                       # retract / lift belonging to the tool change
            err("SLICE_UNKNOWN_MOTION", "XY motion outside object sections and outside the slicer's tool change: %s" % code, i,
                "Unknown motion is not ignorable; this input is not supported.")
            continue
        if head in ("M106", "M107"):
            pending_fans.append(code)
            continue
        if head in ("M104", "M109"):
            continue                                           # standby / print temperature switching (ooze prevention)
        if head in ("G4", "G92", "RESPOND", "M73", "SET_PRINT_STATS_INFO", "TIMELAPSE_TAKE_PHOTO", "G90", "G21", "M83", "M140", "end_print"):
            if head == "G92" and code.replace(" ", "") != "G92E0":
                err("SLICE_UNSUPPORTED_GCODE", "G92 other than E0: %s" % code, i)
            if head == "M83":
                pass
            continue
        if head == "M220":
            if code.replace(" ", "") != "M220S100":
                err("SLICE_UNSUPPORTED_GCODE", "speed factor change %s in tool change" % code, i)
            continue
        if head == "SET_PRESSURE_ADVANCE":
            v = re.search(r"ADVANCE=([-\d.]+)", code)
            if v is None:
                err("SLICE_UNSUPPORTED_GCODE", "bad SET_PRESSURE_ADVANCE", i)
                continue
            v = float(v.group(1))
            if pa is not None and abs(v - pa) > 1e-12:
                err("SLICE_UNSUPPORTED_FEATURE", "pressure advance differs between tools", i)
            pa = v
            continue
        if head == "SET_VELOCITY_LIMIT":
            continue                                           # outside sections: tool-change acceleration, replaced
        err("SLICE_UNSUPPORTED_GCODE", "unsupported command %s outside model sections" % head, i)

    if start is None:
        diags.error("SLICE_STRUCTURE", "no start_print found")
    if used != {0, 1, 2, 3}:
        diags.error("DUAL_TOOLS", "Dual Color Copy Mode needs regions for T0, T1, T2 and T3; slice uses %s" % sorted(used),
                    "Slice two copies of a two-colour model: left colours on T0/T2, right colours on T1/T3.")
    for t in range(4):
        missing = [L for L in range(1, layer + 1) if L not in sections[t]]
        if missing and t in used:
            diags.error("DUAL_MISSING_REGION", "T%d has no region on layer(s) %s" % (t, missing[:8]),
                        "Every layer must contain both colours of both models.")
    if not diags.ok():
        raise StageError(diags)
    return dict(header=header, config_tail=config_tail, cfg=cfg, start=start, pa=pa, sections=sections, fans=fans,
                zs=zs, hs=hs, layers=layer, order=order)


def _strip_tower_departure(sec):
    """Drop the leading retract/wipe a section performs at the slicer's prime tower
    (before its first travel); the generator performs its own tower departure."""
    out = list(sec)
    i = 0
    dropped = 0.0
    while i < len(out):
        s = out[i].strip()
        c = s.split(";", 1)[0].strip()
        if not c or c.startswith("SET_VELOCITY_LIMIT"):
            i += 1
            continue
        if c.startswith(("G1", "G0")):
            w = {k: float(v) for k, v in W.findall(c[2:])}
            if set(w) <= {"F"}:
                i += 1
                continue
            if "E" in w and w["E"] < 0 and "Z" not in w:
                dropped += -w["E"]
                out[i] = ";dropped tower departure: " + c
                i += 1
                continue
        break
    return out, dropped


def _tool_stream(sp, t):
    """Single-tool Co Print stream for tool t (input to the strict reader)."""
    params = dict(re.findall(r"([A-Za-z0-9_]+)=('[^']*'|\S+)", sp["start"]))
    temp = params.get("EXT%d_TEMP" % t, params.get("EXTRUDER_TEMP"))
    bed = params.get("BED_TEMP")
    out = list(sp["header"])
    out.append("start_print EXTRUDER=0 EXTRUDER_TEMP=%s PURGE_LINE='false' BED_TEMP=%s EXT0=true EXT1=false EXT2=false "
               "EXT3=false EXT0_TEMP=%s" % (temp, bed, temp))
    out += ["G90", "G21", "M83"]
    if sp["pa"] is not None:
        out.append("SET_PRESSURE_ADVANCE ADVANCE=%s" % repr(sp["pa"]))
    out.append("T0")
    level = 0.0
    first = True
    for L in range(1, sp["layers"] + 1):
        sec, _dropped = _strip_tower_departure(sp["sections"][t][L])
        # amount primed by this section's approach
        prime = 0.0
        for l in sec:
            c = l.split(";", 1)[0].strip()
            if c.startswith("G1"):
                w = {k: float(v) for k, v in W.findall(c[2:])}
                if "E" in w and not ("X" in w or "Y" in w):
                    if w["E"] > 0:
                        prime += w["E"]
                        continue
                if "E" in w and ("X" in w or "Y" in w) and w["E"] > 0:
                    break
        out += [";LAYER_CHANGE", ";Z:%s" % ("%g" % sp["zs"][L]), ";HEIGHT:%s" % ("%g" % sp["hs"].get(L, 0.2))]
        need = prime - level
        if first:
            out.append("G1 E-%.5f F3000" % prime)
        out += sp["fans"][t].get(L, [])
        if not first and need > 1e-9:
            # placed in the previous section's trailer (after its stop marker)
            idx = len(out) - 3 - len(sp["fans"][t].get(L, []))
            out.insert(idx, "G1 E-%.5f F3000" % need)
        out.append("; printing object region-T%d id:0 copy 0" % t)
        out += sec
        out.append("; stop printing object region-T%d id:0 copy 0" % t)
        level = _level_after(sec, 0.0)
        first = False
    out += ["M106 S0", ";TYPE:Custom", "end_print"]
    out += sp["config_tail"]
    return "\n".join(out) + "\n"


def _coverage(m, L, dx=0.0, dy=0.0, res=0.25):
    cells = set()
    for mv in m.blocks[L - 1].moves():
        if mv.kind != "extrude":
            continue
        n = max(1, int(mv.xy_len / (res * 0.4)))
        for k in range(n + 1):
            x = mv.x0 + (mv.x - mv.x0) * k / n + dx
            y = mv.y0 + (mv.y - mv.y0) * k / n + dy
            cells.add((int(round(x / res)), int(round(y / res))))
    return cells


def _dilate(c, r=2):
    return {(a + i, b + j) for a, b in c for i in range(-r, r + 1) for j in range(-r, r + 1)}


def compare_regions(a, b, diags, label, res=0.25, max_mismatch=0.005, max_e=0.01):
    """The right model's region (b) must cover the same area as the left model's
    region (a) translated by one constant vector, on every layer.

    Deposited area is rasterised (0.25 mm cells; differences closer than two
    cells to the other region are ignored as path-order noise). A layer fails if
    more than 0.5 % of either region has no counterpart or the filament amounts
    differ by more than 1 %. Path ORDER may differ: in COPY mode the right head
    reproduces the left head's paths, so only the deposited area must match."""
    ax0, ay0 = a.bbox_extrude[0], a.bbox_extrude[1]
    bx0, by0 = b.bbox_extrude[0], b.bbox_extrude[1]
    d = (bx0 - ax0, by0 - ay0)
    bad = []
    worst = 0.0
    for L in range(1, a.layer_count + 1):
        A = _coverage(a, L, d[0], d[1], res)
        B = _coverage(b, L, 0.0, 0.0, res)
        if not A and not B:
            continue
        miss_a = A - _dilate(B)
        miss_b = B - _dilate(A)
        fa = len(miss_a) / max(1, len(A))
        fb = len(miss_b) / max(1, len(B))
        ea = sum(mv.e for mv in a.blocks[L - 1].moves() if mv.kind == "extrude")
        eb = sum(mv.e for mv in b.blocks[L - 1].moves() if mv.kind == "extrude")
        de = abs(ea - eb) / max(1e-9, max(ea, eb))
        worst = max(worst, fa, fb)
        if fa > max_mismatch or fb > max_mismatch or de > max_e:
            pts = [(c[0] * res - d[0], c[1] * res - d[1]) for c in miss_a] + [(c[0] * res - d[0], c[1] * res - d[1]) for c in miss_b]
            bb = (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)) if pts else None
            bad.append(dict(layer=L, left_only=round(fa, 4), right_only=round(fb, 4), filament_diff=round(de, 4),
                            area_left_model_xy=[round(v, 2) for v in bb] if bb else None))
    if bad:
        layers = [x["layer"] for x in bad]
        bb = bad[0]["area_left_model_xy"]
        diags.error("DUAL_NOT_COPYABLE",
                    "%s: the right model's region differs from the left model's on %d layer(s) (%s%s); first difference around "
                    "x %s-%s, y %s-%s (left model coordinates)" % (label, len(bad), layers[:6], "..." if len(layers) > 6 else "",
                                                                   bb[0], bb[2], bb[1], bb[3]) if bb else label,
                    "COPY prints the left model's paths on the right model too. Slice two identical copies with the same colour "
                    "painting so both models' regions correspond.")
        return None, bad
    return (d, worst), []


def load_dual(path, diags=None, strict=True, right_from_left=False):
    """strict=False returns the parsed input even when the regions do not
    correspond (errors stay in diags) so a preview can show where they differ.

    right_from_left=True (built-in example only, never for a user's own file):
    the LEFT model's T0 and T2 regions are the single source; the right copy is
    made from them (as the successful reference print did: right paths = left
    paths translated, layer 4+ by COPY). The slice's own right-model regions are
    compared and the differences reported as replaced, not as an error."""
    if right_from_left:
        d0 = Diagnostics()
        ds, dd = load_dual(path, d0, strict=False)
        for x in dd.items:
            if x.code != "DUAL_NOT_COPYABLE":
                (diags or Diagnostics()).items.append(x) if diags is not None else None
        hard = [x for x in dd.errors if x.code != "DUAL_NOT_COPYABLE"]
        if hard:
            d1 = diags or Diagnostics()
            if diags is None:
                d1.items.extend(hard)
            raise StageError(d1)
        ds.right_replaced = ds.region_differences
        ds.region_differences = dict(colour1=[], colour2=[])
        ds.slice_translation = tuple(ds.translation)
        ds.models = {0: ds.models[0], 1: ds.models[0], 2: ds.models[2], 3: ds.models[2]}
        ds.translation = (0.0, 0.0)
        ds.right_source = "left"
        ds.stats = dict(ds.stats, right_source="left model T0/T2 regions (example)", copy_max_dev=0.0)
        return ds, (diags or Diagnostics())
    diags = diags or Diagnostics()
    sp = split_tools(path, diags)
    cfg = sp["cfg"]
    models = {}
    tmp = tempfile.mkdtemp(prefix="dual_")
    try:
        for t in range(4):
            p = os.path.join(tmp, "T%d.gcode" % t)
            with open(p, "w") as f:
                f.write(_tool_stream(sp, t))
            d1 = Diagnostics()
            try:
                m, d1 = load_slice(p, d1)
            except StageError as e:
                for x in e.diags.items:
                    x.message = "T%d region: %s" % (t, x.message)
                    diags.items.append(x)
                continue
            m.path = path
            models[t] = m
    finally:
        for f in os.listdir(tmp):
            os.remove(os.path.join(tmp, f))
        os.rmdir(tmp)
    if not diags.ok():
        raise StageError(diags)
    ra, bad_a = compare_regions(models[0], models[1], diags, "colour 1 (T0 left / T1 right)")
    rb, bad_b = compare_regions(models[2], models[3], diags, "colour 2 (T2 left / T3 right)")
    region_differences = dict(colour1=bad_a, colour2=bad_b)
    if not diags.ok():
        if strict:
            e = StageError(diags)
            e.region_differences = region_differences
            raise e
        ra = ra or ((models[1].bbox_extrude[0] - models[0].bbox_extrude[0], models[1].bbox_extrude[1] - models[0].bbox_extrude[1]), 1.0)
        rb = rb or ra
    if diags.ok() and math.hypot(ra[0][0] - rb[0][0], ra[0][1] - rb[0][1]) > 0.002:
        diags.error("DUAL_NOT_COPYABLE", "the right model's two colours are not translated by the same vector (%.3f, %.3f) vs (%.3f, %.3f)"
                    % (ra[0][0], ra[0][1], rb[0][0], rb[0][1]))
        raise StageError(diags)
    zs = [b.z for b in models[0].blocks]
    for t in (1, 2, 3):
        if [b.z for b in models[t].blocks] != zs:
            diags.error("DUAL_LAYERS", "T%d layer heights differ from T0" % t)
    # both colours of the left model must share one slice frame and not overlap in the same layer
    colours = (cfg.get("filament_colour", "") or "").split(";")
    colours = {t: (colours[t] if t < len(colours) and colours[t] else "#888888") for t in range(4)}
    try:
        preheat = float(str(cfg.get("preheat_time", "0")).split(",")[0])
    except ValueError:
        preheat = 0.0
    try:
        delta = float(str(cfg.get("standby_temperature_delta", "0")).split(",")[0])
    except ValueError:
        delta = 0.0
    if not diags.ok() and strict:
        raise StageError(diags)
    stats = dict(layers=len(zs), moves={t: models[t].stats["block_moves"] for t in range(4)},
                 copy_translation=[round(ra[0][0], 4), round(ra[0][1], 4)], copy_max_dev=max(ra[1], rb[1]))
    ds = DualSlice(path, models, colours, preheat, delta, ra[0], cfg, sp["order"], stats)
    ds.region_differences = region_differences
    ds.right_replaced = None
    ds.right_source = "slice"
    return ds, diags
