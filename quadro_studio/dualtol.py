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

* small_negative_room: on extrusion moves where the colours stack or meet, a negative local room
  of less than 0.050 mm is an accepted deviation (warning). It never relaxes travel, wipe,
  descent, park or carriage checks, nor a flow mismatch where both models have positive room.

A value equal to its limit passes (small_negative_room: strictly smaller passes). EPS only absorbs G-code rounding (XY 4, Z 5 decimals).
"""
from __future__ import annotations

DUAL_LIMITS = dict(y_registration=0.30, z_deviation=0.30, into_material=0.03, small_negative_room=0.050)
EPS = 1e-4


def small_negative(room, limit=None):
    """Owner rule (all models and profiles): on an EXTRUSION move where the colours stack or their
    borders meet, a local room that is negative but smaller than 0.050 mm in size is an accepted
    quality deviation (a warning, not a blocker). Exactly 0.050 mm or more is not."""
    lim = DUAL_LIMITS["small_negative_room"] if limit is None else limit
    return -lim < room < 0.0


def within(value, limit):
    """True if |value| <= limit (equality passes)."""
    return abs(value) <= limit + EPS


def exceeds(value, limit):
    return not within(value, limit)
