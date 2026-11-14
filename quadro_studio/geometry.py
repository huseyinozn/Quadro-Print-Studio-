"""Small 2-D geometry helpers (no third-party dependencies)."""
from __future__ import annotations

import math


def seg_point_dist(p, a, b):
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _segs_intersect(a, b, c, d):
    def orient(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return (v > 1e-12) - (v < -1e-12)
    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    return o1 != o2 and o3 != o4 and o1 != 0 and o3 != 0


def point_in_convex(p, poly):
    n = len(poly)
    if n < 3:
        return False
    sign = 0
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        c = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
        s = (c > 0) - (c < 0)
        if s == 0:
            continue
        if sign == 0:
            sign = s
        elif s != sign:
            return False
    return True


def poly_distance(P, Q):
    """Minimum distance between two convex polygons (0 if overlapping)."""
    if point_in_convex(P[0], Q) or point_in_convex(Q[0], P):
        return 0.0
    best = float("inf")
    for i in range(len(P)):
        a, b = P[i], P[(i + 1) % len(P)]
        for j in range(len(Q)):
            c, d = Q[j], Q[(j + 1) % len(Q)]
            if _segs_intersect(a, b, c, d):
                return 0.0
            best = min(best, seg_point_dist(a, c, d), seg_point_dist(b, c, d),
                       seg_point_dist(c, a, b), seg_point_dist(d, a, b))
    return best


def translate(poly, dx, dy):
    return [(x + dx, y + dy) for x, y in poly]


def bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def bbox_gap(a, b):
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return math.hypot(dx, dy)


def flow_area(h, w):
    """Slic3r/Orca extrusion cross-section (rounded rectangle) for height h, width w."""
    return (w - h * (1.0 - math.pi / 4.0)) * h
