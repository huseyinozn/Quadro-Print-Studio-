"""Printer profile loading (read-only, never executed).

A *profile* is an explicit, user-supplied archive (or directory) holding the
Klipper configuration of one specific Quadro: ``printer.cfg`` with its saved
bed mesh, the included head configs, and ``bed_pin_offsets.json`` with the
measured per-tool XY/Z corrections.

Security: archive expansion is bounded (entry count, per-file and total
size, compression ratio) and unsafe paths (absolute, ``..``, drive letters,
symlinks, device files) are rejected. Config and macro text is parsed as
data only; Jinja/macro bodies are never rendered or executed.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import posixpath
import re
import stat
import zipfile
from dataclasses import dataclass, field

from .diagnostics import Diagnostics, StageError
from .mesh import BedMesh, MeshError

MAX_ENTRIES = 256
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 24 * 1024 * 1024
MAX_RATIO = 200.0
ALLOWED_SUFFIXES = (".cfg", ".json", ".conf", ".txt", ".md")

TOOLS = (0, 1, 2, 3)
CARRIAGE_SECTIONS = {0: "carriage carriage_t0", 1: "dual_carriage carriage_t1",
                     2: "dual_carriage carriage_t2", 3: "dual_carriage carriage_t3"}
EXTRUDER_NAMES = {0: "extruder", 1: "extruder1", 2: "extruder2", 3: "extruder3"}


# --------------------------------------------------------------------------
# Safe archive reading
# --------------------------------------------------------------------------
def _safe_name(name):
    if not name or "\x00" in name or "\\" in name:
        return None
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return None
    norm = posixpath.normpath(name)
    if norm.startswith("../") or norm == ".." or "/../" in ("/" + norm + "/"):
        return None
    return norm


def read_archive(path, diags: Diagnostics):
    """Return {relative_name: bytes} for an archive or directory."""
    files = {}
    if os.path.isdir(path):
        total = 0
        for root, dirs, names in os.walk(path, followlinks=False):
            for n in names:
                full = os.path.join(root, n)
                rel = os.path.relpath(full, path).replace(os.sep, "/")
                if os.path.islink(full):
                    diags.error("PROFILE_UNSAFE_PATH", "symlink rejected in profile: %s" % rel)
                    continue
                if not rel.lower().endswith(ALLOWED_SUFFIXES):
                    continue
                size = os.path.getsize(full)
                total += size
                if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                    diags.error("PROFILE_TOO_LARGE", "profile exceeds size bounds at %s" % rel)
                    return files
                with open(full, "rb") as f:
                    files[rel] = f.read()
        return files
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        diags.error("PROFILE_UNREADABLE", "cannot open profile archive: %s" % e,
                    "Provide the printer profile as a .zip of the Klipper config folder.")
        return files
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            diags.error("PROFILE_TOO_MANY_ENTRIES", "archive has %d entries (limit %d)" % (len(infos), MAX_ENTRIES))
            return files
        total = 0
        for info in infos:
            name = _safe_name(info.filename)
            mode = (info.external_attr >> 16) & 0xFFFF
            if name is None:
                diags.error("PROFILE_UNSAFE_PATH", "unsafe archive path rejected: %r" % info.filename)
                continue
            if mode and (stat.S_ISLNK(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode)
                         or stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)):
                diags.error("PROFILE_UNSAFE_PATH", "non-regular archive entry rejected: %s" % name)
                continue
            if info.is_dir():
                continue
            if info.file_size > MAX_FILE_BYTES:
                diags.error("PROFILE_TOO_LARGE", "%s is %d bytes (limit %d)" % (name, info.file_size, MAX_FILE_BYTES))
                continue
            if info.compress_size and info.file_size / max(1, info.compress_size) > MAX_RATIO:
                diags.error("PROFILE_ZIP_BOMB", "%s has a suspicious compression ratio" % name)
                continue
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                diags.error("PROFILE_TOO_LARGE", "profile archive expands beyond %d bytes" % MAX_TOTAL_BYTES)
                break
            if not name.lower().endswith(ALLOWED_SUFFIXES):
                continue
            with zf.open(info) as fh:
                data = fh.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                diags.error("PROFILE_TOO_LARGE", "%s expands beyond its declared size" % name)
                continue
            files[name] = data
            files.setdefault("__zipinfo__", {})[name] = "%04d-%02d-%02d %02d:%02d" % info.date_time[:5]
    return files


# --------------------------------------------------------------------------
# Klipper config parsing (data only)
# --------------------------------------------------------------------------
def parse_cfg(text):
    """Parse Klipper-style config text into {section: {key: value}}.

    Handles ``key: value`` / ``key = value``, indented continuation lines and
    comments. Macro bodies are kept as opaque strings and never evaluated.
    """
    sections = {}
    order = []
    cur = None
    last_key = None
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            if cur is not None and last_key and raw[:1] in (" ", "\t") and stripped and not stripped.startswith(("#", ";")):
                pass
            continue
        m = re.match(r"^\[([^\]]+)\]\s*(?:[#;].*)?$", stripped)
        if m and raw[:1] not in (" ", "\t"):
            cur = m.group(1).strip()
            sections.setdefault(cur, {})
            order.append(cur)
            last_key = None
            continue
        if cur is None:
            continue
        if raw[:1] in (" ", "\t") and last_key is not None:
            sections[cur][last_key] += "\n" + stripped
            continue
        m = re.match(r"^([A-Za-z0-9_\-\. ]+?)\s*[:=]\s*(.*)$", stripped)
        if m:
            key = m.group(1).strip().lower()
            val = re.split(r"\s+[#;]", m.group(2), maxsplit=1)[0].strip()
            sections[cur][key] = val
            last_key = key
    return sections, order


def split_save_config(text):
    """Return (main_text, save_config_text)."""
    main, saved = [], []
    in_saved = False
    for line in text.splitlines():
        if line.startswith("#*# <") and "SAVE_CONFIG" in line:
            in_saved = True
            continue
        if in_saved and line.startswith("#*#"):
            saved.append(line[3:].lstrip(" ") if not line[3:4] == "\t" else line[3:])
        elif in_saved and line.strip() == "":
            continue
        elif not in_saved:
            main.append(line)
    # SAVE_CONFIG uses "#*# " prefix followed by original indentation
    fixed = []
    for line in saved:
        fixed.append(line)
    return "\n".join(main), "\n".join(fixed)


def _f(sec, key, diags, where, default=None, required=True):
    if key in sec:
        try:
            return float(sec[key].split(",")[0])
        except ValueError:
            diags.error("PROFILE_BAD_VALUE", "%s: %s is not a number (%r)" % (where, key, sec[key]))
            return default
    if required and default is None:
        diags.error("PROFILE_MISSING_VALUE", "%s: missing %s" % (where, key))
    return default


@dataclass
class Carriage:
    tool: int
    name: str
    position_min: float
    position_max: float
    position_endstop: float


@dataclass
class Extruder:
    tool: int
    name: str
    nozzle_diameter: float
    filament_diameter: float
    rotation_distance: float
    min_extrude_temp: float
    max_temp: float
    max_extrude_only_distance: float
    fan: str


@dataclass
class ToolOffset:
    x: float
    y: float
    z: float


@dataclass
class Profile:
    source: str
    carriages: dict
    extruders: dict
    offsets: dict
    mesh: BedMesh
    mesh_name: str
    y_range: tuple
    z_range: tuple
    max_velocity: float
    max_accel: float
    max_z_velocity: float
    min_clearance: float
    bed_max_temp: float
    fade_start: float
    fade_end: float
    macros: set
    sections: set
    fingerprint: str
    file_hashes: dict = field(default_factory=dict)
    file_dates: dict = field(default_factory=dict)

    def summary(self):
        return {
            "source": os.path.basename(self.source),
            "fingerprint": self.fingerprint,
            "carriages": {t: [c.position_min, c.position_max, c.position_endstop] for t, c in self.carriages.items()},
            "offsets": {"T%d" % t: [o.x, o.y, o.z] for t, o in self.offsets.items()},
            "mesh": {"name": self.mesh_name, "x": [self.mesh.min_x, self.mesh.max_x, self.mesh.x_count],
                     "y": [self.mesh.min_y, self.mesh.max_y, self.mesh.y_count], "algo": self.mesh.algo,
                     "pps": [self.mesh.x_pps, self.mesh.y_pps], "tension": self.mesh.tension,
                     "z_range": [min(min(r) for r in self.mesh.points), max(max(r) for r in self.mesh.points)]},
            "y_range": list(self.y_range), "z_range": list(self.z_range),
            "max_velocity": self.max_velocity, "max_accel": self.max_accel,
            "min_clearance": self.min_clearance,
            "extruders": {e.name: {"rotation_distance": e.rotation_distance, "nozzle": e.nozzle_diameter,
                                   "filament": e.filament_diameter, "fan": e.fan} for e in self.extruders.values()},
            "archive_dates": self.file_dates,
        }


REQUIRED_SECTIONS = ["printer", "quadro_parallel_carriage", "carriage carriage_y", "carriage carriage_z",
                     "heater_bed"] + list(CARRIAGE_SECTIONS.values()) + list(EXTRUDER_NAMES.values())


def load_profile(path, mesh_name=None, offsets_file=None):
    diags = Diagnostics()
    if not os.path.exists(path):
        diags.error("PROFILE_MISSING", "printer profile not found: %s" % path,
                    "Pass --profile with the printer's config archive.")
        raise StageError(diags)
    files = read_archive(path, diags)
    dates = files.pop("__zipinfo__", {})
    if not diags.ok():
        raise StageError(diags)
    printer_cfgs = [n for n in files if posixpath.basename(n) == "printer.cfg"]
    if len(printer_cfgs) != 1:
        diags.error("PROFILE_PRINTER_CFG", "expected exactly one printer.cfg in profile, found %d" % len(printer_cfgs),
                    "The profile must contain the printer's current printer.cfg (with its SAVE_CONFIG block).")
        raise StageError(diags)
    base = posixpath.dirname(printer_cfgs[0])
    try:
        main_text = files[printer_cfgs[0]].decode("utf-8")
    except UnicodeDecodeError:
        diags.error("PROFILE_ENCODING", "printer.cfg is not UTF-8")
        raise StageError(diags)
    main, saved_text = split_save_config(main_text)

    sections = {}
    macros = set()
    seen = set()

    def include(rel_text, origin):
        secs, order = parse_cfg(rel_text)
        for name in order:
            if name.startswith("include "):
                target = name[len("include "):].strip()
                if any(ch in target for ch in "*?["):
                    diags.warning("PROFILE_INCLUDE_GLOB", "glob include %r not expanded" % target)
                    continue
                full = posixpath.normpath(posixpath.join(base, target))
                if full in seen:
                    continue
                seen.add(full)
                if full not in files:
                    diags.error("PROFILE_INCLUDE_MISSING", "%s includes %s which is not in the profile" % (origin, target),
                                "Add the included file to the profile archive.")
                    continue
                include(files[full].decode("utf-8", errors="replace"), target)
                continue
            if name.startswith("gcode_macro "):
                macros.add(name[len("gcode_macro "):].strip().upper())
            sections.setdefault(name, {}).update(secs[name])

    include(main, "printer.cfg")
    saved, saved_order = parse_cfg(saved_text)
    for name in saved_order:
        sections.setdefault(name, {}).update(saved[name])

    for req in REQUIRED_SECTIONS:
        if req not in sections:
            diags.error("PROFILE_SECTION_MISSING", "profile lacks [%s]" % req,
                        "Farm Mode needs four X carriages, four extruders and the quadro_parallel_carriage module.")
    if not diags.ok():
        raise StageError(diags)
    kin = sections["printer"].get("kinematics", "")
    if kin not in ("generic_cartesian", "cartesian"):
        diags.error("PROFILE_KINEMATICS", "unsupported kinematics %r" % kin)

    carriages = {}
    for t, sec in CARRIAGE_SECTIONS.items():
        s = sections[sec]
        carriages[t] = Carriage(t, sec.split()[1], _f(s, "position_min", diags, sec),
                                _f(s, "position_max", diags, sec), _f(s, "position_endstop", diags, sec))
    extruders = {}
    for t, name in EXTRUDER_NAMES.items():
        s = sections[name]
        fan = "fan_t%d" % t
        if ("fan_generic %s" % fan) not in sections:
            diags.error("PROFILE_FAN_MISSING", "part cooling fan [fan_generic %s] not found" % fan)
        extruders[t] = Extruder(t, name, _f(s, "nozzle_diameter", diags, name), _f(s, "filament_diameter", diags, name),
                                _f(s, "rotation_distance", diags, name), _f(s, "min_extrude_temp", diags, name, 170.0),
                                _f(s, "max_temp", diags, name), _f(s, "max_extrude_only_distance", diags, name, 50.0),
                                fan)
    yc = sections["carriage carriage_y"]
    zc = sections["carriage carriage_z"]
    pr = sections["printer"]
    qpc = sections["quadro_parallel_carriage"]
    bm = sections.get("bed_mesh", {})

    # --- saved mesh selection -------------------------------------------------
    meshes = [n[len("bed_mesh "):].strip() for n in sections if n.startswith("bed_mesh ")]
    mesh = None
    chosen = None
    if not meshes:
        diags.error("PROFILE_MESH_MISSING", "no saved bed mesh found in printer.cfg SAVE_CONFIG block",
                    "Run the printer's bed mesh calibration, SAVE_CONFIG, and export the profile again.")
    else:
        if mesh_name:
            if mesh_name not in meshes:
                diags.error("PROFILE_MESH_MISSING", "requested mesh profile %r not found (have: %s)" % (mesh_name, ", ".join(meshes)))
            chosen = mesh_name
        elif len(meshes) == 1:
            chosen = meshes[0]
        else:
            diags.error("PROFILE_MESH_AMBIGUOUS", "several saved meshes found (%s); refusing to guess" % ", ".join(meshes),
                        "Pass --mesh-profile NAME to select the mesh measured for this print.")
    if chosen and chosen in meshes:
        s = sections["bed_mesh " + chosen]
        try:
            pts = [[float(v) for v in row.split(",") if v.strip()] for row in s.get("points", "").split("\n") if row.strip()]
            mesh = BedMesh(pts, float(s["min_x"]), float(s["max_x"]), float(s["min_y"]), float(s["max_y"]),
                           int(s["x_count"]), int(s["y_count"]), int(s.get("mesh_x_pps", 2)), int(s.get("mesh_y_pps", 2)),
                           s.get("algo", "lagrange").strip(), float(s.get("tension", 0.2)), chosen)
            if int(s.get("version", 1)) != 1:
                diags.error("PROFILE_MESH_VERSION", "unsupported saved mesh version %s" % s.get("version"))
        except (KeyError, ValueError, MeshError) as e:
            diags.error("PROFILE_MESH_INVALID", "saved mesh %r cannot be read: %s" % (chosen, e))
            mesh = None

    # --- tool offsets ---------------------------------------------------------
    offsets = {}
    cand = [n for n in files if posixpath.basename(n) == (offsets_file or "bed_pin_offsets.json")]
    if len(cand) != 1:
        diags.error("PROFILE_OFFSETS_MISSING" if not cand else "PROFILE_OFFSETS_AMBIGUOUS",
                    "expected one bed_pin_offsets.json with measured tool offsets, found %d" % len(cand),
                    "Run the tool-offset calibration and include bed_pin_offsets.json in the profile.")
    else:
        try:
            data = json.loads(files[cand[0]].decode("utf-8"))
            for t in TOOLS:
                o = data["T%d" % t]
                offsets[t] = ToolOffset(float(o["x"]), float(o["y"]), float(o["z"]))
        except (ValueError, KeyError, TypeError) as e:
            diags.error("PROFILE_OFFSETS_INVALID", "bed_pin_offsets.json is malformed: %s" % e,
                        "It must contain T0..T3 each with numeric x, y, z.")
        if offsets:
            o0 = offsets[0]
            if abs(o0.x) > 1e-9 or abs(o0.y) > 1e-9 or abs(o0.z) > 1e-9:
                diags.error("PROFILE_OFFSETS_REFERENCE", "T0 offset must be the zero reference (got %s)" % o0)
            for t, o in offsets.items():
                if abs(o.x) > 5 or abs(o.y) > 5 or abs(o.z) > 2:
                    diags.error("PROFILE_OFFSETS_IMPLAUSIBLE", "T%d offset %s exceeds plausible calibration range" % (t, o),
                                "Re-run the tool offset calibration; offsets larger than 5 mm XY / 2 mm Z are rejected.")

    min_clear = _f(qpc, "min_clearance", diags, "quadro_parallel_carriage", 56.25, required=False)
    fade_start = _f(bm, "fade_start", diags, "bed_mesh", 1.0, required=False)
    fade_end = _f(bm, "fade_end", diags, "bed_mesh", 0.0, required=False)

    if not diags.ok():
        raise StageError(diags)

    h = hashlib.sha256()
    file_hashes = {}
    for n in sorted(files):
        file_hashes[n] = hashlib.sha256(files[n]).hexdigest()[:16]
        h.update(n.encode())
        h.update(files[n])
    prof = Profile(
        source=path, carriages=carriages, extruders=extruders, offsets=offsets, mesh=mesh, mesh_name=chosen,
        y_range=(_f(yc, "position_min", diags, "carriage_y"), _f(yc, "position_max", diags, "carriage_y")),
        z_range=(_f(zc, "position_min", diags, "carriage_z"), _f(zc, "position_max", diags, "carriage_z")),
        max_velocity=_f(pr, "max_velocity", diags, "printer"), max_accel=_f(pr, "max_accel", diags, "printer"),
        max_z_velocity=_f(pr, "max_z_velocity", diags, "printer", 5.0, required=False),
        min_clearance=min_clear, bed_max_temp=_f(sections["heater_bed"], "max_temp", diags, "heater_bed"),
        fade_start=fade_start, fade_end=fade_end, macros=macros, sections=set(sections),
        fingerprint=h.hexdigest()[:16], file_hashes=file_hashes, file_dates=dates)
    return prof, diags


def profile_checks(prof: Profile, diags: Diagnostics):
    """Non-fatal consistency checks surfaced to the user."""
    mesh = prof.mesh
    outs = mesh.outliers()
    for o in outs:
        diags.warning("MESH_DEVIATING_POINT", "saved mesh point (%.2f, %.2f) = %.4f mm deviates markedly (%.3f mm) from its "
                      "neighbours' median" % (o["x"], o["y"], o["z"], o["deviation"]),
                      "Used exactly as measured (never modified). Copies that depend on it are listed in the plan; "
                      "check the bed at this point before printing.", ix=o["ix"], iy=o["iy"])
    # Klipper refuses meshes extending past the fade distance
    if prof.fade_end > prof.fade_start:
        zr = max(abs(min(min(r) for r in mesh.points)), abs(max(max(r) for r in mesh.points)))
        if zr >= prof.fade_end - prof.fade_start:
            diags.error("MESH_FADE_RANGE", "mesh range exceeds fade distance")
    if not prof.file_dates:
        diags.info("PROFILE_DATES", "profile loaded from a directory; file dates not available")
    else:
        stamps = sorted(set(prof.file_dates.values()))
        diags.warning("PROFILE_FRESHNESS_UNVERIFIED",
                      "profile archive timestamps (%s) cannot prove that mesh and tool offsets were measured on the "
                      "current machine state" % ", ".join(stamps),
                      "Confirm the mesh and offsets are current before printing; regenerate after any re-calibration.")
    for name in ("_PRINT_HOME", "M109", "M190"):
        if name not in prof.macros:
            diags.error("PROFILE_MACRO_MISSING", "required macro %s not defined in profile" % name)
    return outs
