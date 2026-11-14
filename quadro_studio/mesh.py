"""Klipper-compatible bed mesh evaluation.

This is a clean re-implementation of the interpolation used by Klipper's
``bed_mesh.ZMesh`` (bicubic / lagrange / direct upsampling followed by
bilinear lookup on the upsampled grid, with clamping outside the mesh).
It is pure arithmetic: no config or macro text is ever executed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


class MeshError(ValueError):
    pass


def _lerp(t, a, b):
    return (1.0 - t) * a + t * b


def _constrain(v, lo, hi):
    return max(lo, min(hi, v))


@dataclass
class BedMesh:
    points: list            # probed matrix [y][x]
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    x_count: int
    y_count: int
    x_pps: int = 2
    y_pps: int = 2
    algo: str = "bicubic"
    tension: float = 0.2
    name: str = "default"
    matrix: list = field(default=None, repr=False)

    def __post_init__(self):
        if len(self.points) != self.y_count or any(len(r) != self.x_count for r in self.points):
            raise MeshError("bed mesh point matrix does not match x_count/y_count")
        for row in self.points:
            for v in row:
                if not math.isfinite(v):
                    raise MeshError("bed mesh contains a non-finite value")
        if self.algo not in ("bicubic", "lagrange", "direct"):
            raise MeshError("unsupported bed mesh algorithm %r" % self.algo)
        if self.algo == "direct":
            self.x_pps = self.y_pps = 0
        self.mx_count = (self.x_count - 1) * self.x_pps + self.x_count
        self.my_count = (self.y_count - 1) * self.y_pps + self.y_count
        self.x_mult = self.x_pps + 1
        self.y_mult = self.y_pps + 1
        self.x_dist = (self.max_x - self.min_x) / (self.mx_count - 1)
        self.y_dist = (self.max_y - self.min_y) / (self.my_count - 1)
        if self.algo == "bicubic":
            if self.x_count < 4 or self.y_count < 4:
                raise MeshError("bicubic mesh needs at least 4x4 probe points")
            self._sample_bicubic()
        elif self.algo == "lagrange":
            self._sample_lagrange()
        else:
            self.matrix = [list(r) for r in self.points]

    # -- upsampling (mirrors Klipper ZMesh) --------------------------------
    def _seed(self):
        xm, ym = self.x_mult, self.y_mult
        return [[0.0 if ((i % xm) or (j % ym)) else self.points[j // ym][i // xm]
                 for i in range(self.mx_count)] for j in range(self.my_count)]

    def _sample_bicubic(self):
        self.matrix = m = self._seed()
        c = self.tension
        for y in range(self.my_count):
            if y % self.y_mult:
                continue
            for x in range(self.mx_count):
                if x % self.x_mult == 0:
                    continue
                m[y][x] = self._spline(self._ctl(m[y], x, self.x_mult, self.mx_count), c)
        for x in range(self.mx_count):
            col = [m[y][x] for y in range(self.my_count)]
            for y in range(self.my_count):
                if y % self.y_mult == 0:
                    continue
                m[y][x] = self._spline(self._ctl(col, y, self.y_mult, self.my_count), c)

    @staticmethod
    def _ctl(row, x, mult, count):
        last = count - 1 - mult
        if x < mult:
            return row[0], row[0], row[mult], row[2 * mult], x / float(mult)
        if x > last:
            return row[last - mult], row[last], row[last + mult], row[last + mult], (x - last) / float(mult)
        for i in range(mult, last, mult):
            if i < x < i + mult:
                return row[i - mult], row[i], row[i + mult], row[i + 2 * mult], (x - i) / float(mult)
        raise MeshError("bed mesh: error finding control points")

    @staticmethod
    def _spline(p, tension):
        t = p[4]
        t2 = t * t
        t3 = t2 * t
        m1 = tension * (p[2] - p[0])
        m2 = tension * (p[3] - p[1])
        return (p[1] * (2 * t3 - 3 * t2 + 1) + p[2] * (-2 * t3 + 3 * t2)
                + m1 * (t3 - 2 * t2 + t) + m2 * (t3 - t2))

    def _sample_lagrange(self):
        self.matrix = m = self._seed()
        xpts = [self.min_x + self.x_dist * i * self.x_mult for i in range(self.x_count)]
        ypts = [self.min_y + self.y_dist * j * self.y_mult for j in range(self.y_count)]

        def lag(pts, c, getter):
            tot = 0.0
            for i in range(len(pts)):
                n = d = 1.0
                for j in range(len(pts)):
                    if i != j:
                        n *= c - pts[j]
                        d *= pts[i] - pts[j]
                tot += getter(i) * n / d
            return tot
        for i in range(self.my_count):
            if i % self.y_mult:
                continue
            for j in range(self.mx_count):
                if j % self.x_mult:
                    m[i][j] = lag(xpts, self.min_x + self.x_dist * j, lambda k: m[i][k * self.x_mult])
        for i in range(self.mx_count):
            for j in range(self.my_count):
                if j % self.y_mult:
                    m[j][i] = lag(ypts, self.min_y + self.y_dist * j, lambda k: m[k * self.y_mult][i])

    # -- lookup ------------------------------------------------------------
    def _index(self, coord, lo, cnt, dist):
        idx = int(math.floor((coord - lo) / dist))
        idx = _constrain(idx, 0, cnt - 2)
        t = (coord - (lo + dist * idx)) / dist
        return _constrain(t, 0.0, 1.0), idx

    def z(self, x, y):
        tx, xi = self._index(x, self.min_x, self.mx_count, self.x_dist)
        ty, yi = self._index(y, self.min_y, self.my_count, self.y_dist)
        m = self.matrix
        z0 = _lerp(tx, m[yi][xi], m[yi][xi + 1])
        z1 = _lerp(tx, m[yi + 1][xi], m[yi + 1][xi + 1])
        return _lerp(ty, z0, z1)

    def inside(self, x, y, tol=1e-9):
        return (self.min_x - tol <= x <= self.max_x + tol) and (self.min_y - tol <= y <= self.max_y + tol)

    def probe_xy(self, ix, iy):
        xs = (self.max_x - self.min_x) / (self.x_count - 1)
        ys = (self.max_y - self.min_y) / (self.y_count - 1)
        return self.min_x + ix * xs, self.min_y + iy * ys

    def outliers(self, threshold_mm=0.25):
        """Probe points that deviate from the median of their (up to 8)
        neighbours by more than ``threshold_mm``. Reported, never repaired."""
        out = []
        for iy in range(self.y_count):
            for ix in range(self.x_count):
                nb = [self.points[j][i]
                      for j in range(iy - 1, iy + 2) for i in range(ix - 1, ix + 2)
                      if (i, j) != (ix, iy) and 0 <= i < self.x_count and 0 <= j < self.y_count]
                nb.sort()
                med = nb[len(nb) // 2] if len(nb) % 2 else 0.5 * (nb[len(nb) // 2 - 1] + nb[len(nb) // 2])
                dev = self.points[iy][ix] - med
                if abs(dev) > threshold_mm:
                    x, y = self.probe_xy(ix, iy)
                    out.append({"ix": ix, "iy": iy, "x": round(x, 4), "y": round(y, 4),
                                "z": self.points[iy][ix], "neighbour_median": round(med, 6),
                                "deviation": round(dev, 6)})
        return out

    def influence_radius(self):
        """Distance (mm) from a probe point over which it influences the
        interpolated surface (bicubic: two probe intervals each axis)."""
        xs = (self.max_x - self.min_x) / (self.x_count - 1)
        ys = (self.max_y - self.min_y) / (self.y_count - 1)
        k = 2 if self.algo == "bicubic" else (1 if self.algo == "direct" else self.x_count)
        return k * xs, k * ys
