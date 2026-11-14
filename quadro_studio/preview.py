"""SVG layout preview: bed, four heads, every copy footprint and the
synchronized COPY groups (one group = the same slot on all four heads)."""
from __future__ import annotations

from xml.sax.saxutils import escape

HEAD_COLORS = {0: "#2563eb", 1: "#16a34a", 2: "#ea580c", 3: "#9333ea"}


def render_svg(model, prof, plan, title="Quadro Print Studio - Farm Mode layout"):
    S = 2.4          # px per mm
    margin = 60
    bedw, bedh = 300.0, 300.0
    W = int(bedw * S + 2 * margin + 260)
    H = int(bedh * S + 2 * margin + 40)

    def X(x):
        return margin + x * S

    def Y(y):
        return margin + (bedh - y) * S

    out = []
    a = out.append
    a('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" font-family="Helvetica,Arial,sans-serif">' % (W, H, W, H))
    a('<rect width="100%" height="100%" fill="#ffffff"/>')
    a('<text x="%d" y="28" font-size="18" font-weight="bold">%s</text>' % (margin, escape(title)))
    a('<text x="%d" y="48" font-size="12" fill="#444">%d copies = 4 heads x %d slots; footprint %.2f x %.2f mm; layers 1-%d independent per head, %d-%d COPY mode</text>'
      % (margin, len(plan.slots) * 4, len(plan.slots), plan.slice_summary["footprint"][0], plan.slice_summary["footprint"][1],
         plan.foundation_layers, plan.foundation_layers + 1, len(plan.layer_z)))
    # bed and mesh area
    a('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="#f8fafc" stroke="#111" stroke-width="1.5"/>' % (X(0), Y(300), 300 * S, 300 * S))
    m = prof.mesh
    a('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="none" stroke="#94a3b8" stroke-dasharray="4 3"/>'
      % (X(m.min_x), Y(m.max_y), (m.max_x - m.min_x) * S, (m.max_y - m.min_y) * S))
    for i in range(0, 301, 50):
        a('<text x="%.1f" y="%.1f" font-size="10" fill="#666" text-anchor="middle">%d</text>' % (X(i), Y(0) + 14, i))
        a('<text x="%.1f" y="%.1f" font-size="10" fill="#666" text-anchor="end">%d</text>' % (X(0) - 4, Y(i) + 3, i))
    # mesh points that deviate markedly from their neighbours (this printer's data)
    for o in plan.mesh_deviating_points:
        a('<circle cx="%.1f" cy="%.1f" r="7" fill="none" stroke="#dc2626" stroke-width="2"/>' % (X(o["x"]), Y(o["y"])))
        a('<text x="%.1f" y="%.1f" font-size="10" fill="#dc2626" text-anchor="end">deviating mesh point %.3f mm</text>' % (X(o["x"]) - 9, Y(o["y"]) + 18, o["z"]))
    affected = {(a_["tool"], a_["slot"]) for a_ in plan.affected_by_outliers if a_["first_layer_dz_max"] > 0.1}
    # purge lines
    for t in range(4):
        p = plan.purge[t]
        a('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="%.1f" stroke-linecap="round"/>'
          % (X(p["x0"]), Y(p["y"]), X(p["x1"]), Y(p["y"]), HEAD_COLORS[t], max(1.5, plan.purge["width_est"] * S)))
    # copies
    for key, hull in plan.footprints.items():
        t, s = (int(v) for v in key.split("/"))
        pts = " ".join("%.1f,%.1f" % (X(x), Y(y)) for x, y in hull)
        col = HEAD_COLORS[t]
        a('<polygon points="%s" fill="%s" fill-opacity="0.22" stroke="%s" stroke-width="1.2"/>' % (pts, col, col))
        if (t, s) in affected:
            a('<polygon points="%s" fill="none" stroke="#dc2626" stroke-width="2.4" stroke-dasharray="5 3"/>' % pts)
        cx = sum(x for x, _ in hull) / len(hull)
        cy = sum(y for _, y in hull) / len(hull)
        a('<text x="%.1f" y="%.1f" font-size="10" text-anchor="middle" fill="#111">T%d.%d</text>' % (X(cx), Y(cy) + 3, t, s))
    # synchronized groups: connect slot k across the four heads
    for s in plan.slots:
        pts = []
        for t in range(4):
            pts.append((s.cx + plan.sync_pitch * t, s.cy - plan.offsets[t][1]))
        a('<polyline points="%s" fill="none" stroke="#334155" stroke-width="1" stroke-dasharray="2 3"/>'
          % " ".join("%.1f,%.1f" % (X(x), Y(y)) for x, y in pts))
    # legend
    lx = X(300) + 25
    ly = Y(300) + 10
    a('<text x="%.1f" y="%.1f" font-size="13" font-weight="bold">Heads</text>' % (lx, ly))
    for t in range(4):
        y = ly + 22 + t * 20
        a('<rect x="%.1f" y="%.1f" width="14" height="14" fill="%s" fill-opacity="0.5" stroke="%s"/>' % (lx, y - 11, HEAD_COLORS[t], HEAD_COLORS[t]))
        a('<text x="%.1f" y="%.1f" font-size="12">T%d  offset (%.3f, %.3f, %.3f)</text>' % (lx + 20, y, t, *plan.offsets[t]))
    y = ly + 22 + 4 * 20 + 10
    lines = [
        "T<head>.<k> = copy k of head T<head>",
        "Dashed line = COPY group k: T0.k..T3.k",
        "  printed simultaneously from layer %d" % (plan.foundation_layers + 1),
        "  (%.1f mm head spacing)" % plan.sync_pitch,
        "Slot order: serpentine, reversed every layer",
        "Thick lines at front: prime line per head",
        "Dashed grey: measured mesh area",
        "Red ring: mesh point deviating from",
        "  its neighbours (used as measured)",
        "Red dashed copy: first layer depends",
        "  on that point by > 0.1 mm",
        "",
        "Park positions (carriage X):",
        "  home %s" % ", ".join("T%d=%.2f" % (t, plan.park_home[t]) for t in range(4)),
        "COPY carriage offsets from T0:",
        "  %s" % ", ".join("T%d=+%.4f" % (t, plan.copy_carriage_delta[t]) for t in range(1, 4)),
        "",
        "CANDIDATE - software-verified only,",
        "not validated on hardware.",
    ]
    for i, l in enumerate(lines):
        a('<text x="%.1f" y="%.1f" font-size="11" fill="#222">%s</text>' % (lx, y + i * 16, escape(l)))
    a("</svg>")
    return "\n".join(out)
