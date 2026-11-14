"""Synthetic (non-private) Quadro-like printer profile for tests."""
from __future__ import annotations

import io
import json
import zipfile

PRINTER_CFG = """[include heads.cfg]
[include macros.cfg]

[printer]
kinematics: generic_cartesian
max_velocity: 500
max_accel: 10000
max_z_velocity: 20

[carriage carriage_t0]
axis: x
position_endstop: -169
position_min: -169
position_max: 303.25

[dual_carriage carriage_t1]
primary_carriage: carriage_t0
axis: x
position_endstop: -112.75
position_min: -112.75
position_max: 359.50

[dual_carriage carriage_t2]
axis: x
position_endstop: 412.75
position_min: -59.50
position_max: 412.75

[dual_carriage carriage_t3]
primary_carriage: carriage_t2
axis: x
position_endstop: 469
position_min: -3.25
position_max: 469

[carriage carriage_y]
axis: y
position_endstop: -1
position_min: -1
position_max: 318

[carriage carriage_z]
axis: z
position_max: 310
position_min: -5
position_endstop: 0

[heater_bed]
max_temp: 105

[quadro_parallel_carriage]
default_speed: 50
min_clearance: 56.25

[bed_mesh]
mesh_min: 5, 5
mesh_max: 295, 295
probe_count: 9,9
algorithm: bicubic
fade_start: 5
fade_end: 15

#*# <---------------------- SAVE_CONFIG ---------------------->
#*# DO NOT EDIT THIS BLOCK OR BELOW. The contents are auto-generated.
#*#
{MESHES}"""

HEADS = "\n".join("""[extruder%s]
nozzle_diameter: 0.400
filament_diameter: 1.750
rotation_distance: 4.5
min_extrude_temp: 150
max_temp: 300
max_extrude_only_distance: 300

[fan_generic fan_t%d]
pin: x
""" % ("" if t == 0 else str(t), t) for t in range(4))

MACROS = """[gcode_macro _PRINT_HOME]
gcode:
    G28

[gcode_macro M109]
rename_existing: M109.1
gcode:
    M104

[gcode_macro M190]
rename_existing: M190.1
gcode:
    M140
"""


def mesh_block(name="default", fn=None, outlier=None, extra_outliers=()):
    fn = fn or (lambda x, y: 0.0004 * (x - 150) - 0.0003 * (y - 150) + 0.000002 * (x - 150) * (y - 150))
    rows = []
    for j in range(9):
        y = 5 + j * 36.25
        vals = []
        for i in range(9):
            x = 5 + i * 36.25
            v = fn(x, y)
            for ol in ([outlier] if outlier else []) + list(extra_outliers):
                if (i, j) == ol[0]:
                    v += ol[1]
            vals.append("%.6f" % v)
        rows.append("#*# \t  " + ", ".join(vals))
    return "\n".join(["#*# [bed_mesh %s]" % name, "#*# version = 1", "#*# points ="] + rows + [
        "#*# x_count = 9", "#*# y_count = 9", "#*# mesh_x_pps = 2", "#*# mesh_y_pps = 2", "#*# algo = bicubic",
        "#*# tension = 0.2", "#*# min_x = 5.0", "#*# max_x = 295.0", "#*# min_y = 5.0", "#*# max_y = 295.0", "#*#"])


OFFSETS = {"T0": {"x": 0.0, "y": 0.0, "z": 0.0}, "T1": {"x": 0.06, "y": -0.25, "z": 0.15},
           "T2": {"x": -1.55, "y": -0.05, "z": 0.10}, "T3": {"x": -1.74, "y": 0.12, "z": 0.02}}


def build(path, meshes=None, offsets=OFFSETS, drop=(), extra=None):
    meshes = meshes if meshes is not None else [mesh_block()]
    files = {
        "config/printer.cfg": PRINTER_CFG.replace("{MESHES}", "\n".join(meshes)),
        "config/heads.cfg": HEADS,
        "config/macros.cfg": MACROS,
    }
    if offsets is not None:
        files["config/bed_pin_offsets.json"] = json.dumps(offsets)
    for d in drop:
        files.pop(d, None)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, t in files.items():
            z.writestr(n, t)
        for n, t in (extra or {}).items():
            z.writestr(n, t)
    return path
