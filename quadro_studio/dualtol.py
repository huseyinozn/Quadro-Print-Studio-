"""Dual Color Copy Mode limits, shared by the planner (dualplan/dualz) and the
independent verifier (verify_dual). One definition, one comparison rule.

* y_registration: allowed absolute Y offset between the two colours of one model
  (pair Y residual), mm. Owner decision: 0.30 mm.
* z_deviation: allowed absolute deviation of the local layer thickness from the
  nominal layer height where one colour is printed on the other, mm. Owner
  decision: 0.30 mm.
* into_material: a nozzle may be at most this far below the top of material
  already printed by the OTHER colour (extruding, travelling, wiping or
  descending), mm. Unchanged; the same value as the verifier's existing
  in-part sink tolerance. Separate from z_deviation and never relaxed by it.

A value equal to its limit passes. EPS only absorbs G-code rounding (XY 4, Z 5 decimals).
"""
from __future__ import annotations

DUAL_LIMITS = dict(y_registration=0.30, z_deviation=0.30, into_material=0.03)
EPS = 1e-4


def within(value, limit):
    """True if |value| <= limit (equality passes)."""
    return abs(value) <= limit + EPS


def exceeds(value, limit):
    return not within(value, limit)
