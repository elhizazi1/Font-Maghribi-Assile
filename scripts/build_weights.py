#!/usr/bin/env python3
"""Build the Maghribi Assile weight family from the Regular master.

Pipeline
--------
1. Read the original Regular outlines (fonts/ttf/MaghribiAssile-Regular.ttf).
2. Convert every contour to an explicit on-curve/off-curve node list.  This is
   deterministic, so every master gets the *same point structure*.
3. Generate two extra masters by offsetting the outlines (Thin and Black).
   Each point moves along its outline normal by a distance that adapts to the
   local stroke thickness and to the free space next to it, so thin marks do
   not collapse and counters do not close.
4. Keep the sidebearings of Regular: every glyph box grows/shrinks by the same
   offset on both sides, so Arabic joins keep their original overlap.
5. Build a variable font (wght 100-900) from Thin / Regular / Black and cut
   the nine static weights out of it.

Run:  python scripts/build_weights.py
Needs: fonttools[woff], numpy, scipy
"""
from __future__ import annotations

import copy
import math
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables._g_l_y_f import Glyph, GlyphCoordinates
from fontTools.ttLib.tables.ttProgram import Program

ROOT = Path(__file__).resolve().parents[1]
REGULAR = ROOT / "fonts/ttf/MaghribiAssile-Regular.ttf"

FAMILY = "Maghribi Assile"
PS_FAMILY = "MaghribiAssile"
VERSION = "1.000"          # must match the shipped Regular (left untouched)

# ---------------------------------------------------------------- parameters
# Outline offset in font units (upem 2048).  Regular stems are ~140 units.
D_THIN = -46.0             # Thin master  -> stem ~ 48
D_BLACK = +112.0           # Black master -> stem ~ 364
K_THIN = 0.35              # never remove more than 35 % of local thickness per side
K_BLACK = 0.80             # never add more than 80 % of local thickness per side
GAP_FACTOR = 0.38          # a point never travels more than 38 % of the free gap in front of it
PX = 4.0                   # raster pixel size (font units) used for measuring
MITER_LIMIT = 2.2
THIN_JOIN_BIAS = 0.35      # thin hairline joins get extra overlap (fraction of |offset|)
END_ZONE = 150.0           # glyph-end zone used to keep join overlap

WEIGHTS = [  # (usWeightClass, style name)
    (100, "Thin"), (200, "ExtraLight"), (300, "Light"), (400, "Regular"),
    (500, "Medium"), (600, "SemiBold"), (700, "Bold"), (800, "ExtraBold"),
    (900, "Black"),
]

# ------------------------------------------------------------ outline model
# A contour is a list of nodes [P, C]: P=(x,y) on-curve point, C=(x,y) or None
# is the quadratic control point between this node and the next one.


def tt_to_nodes(pts, on):
    n = len(pts)
    pts = [tuple(map(float, p)) for p in pts]
    on = list(on)
    if not any(on):
        start = ((pts[-1][0] + pts[0][0]) / 2, (pts[-1][1] + pts[0][1]) / 2)
        pts = [start] + pts
        on = [True] + on
    else:
        k = on.index(True)
        pts = pts[k:] + pts[:k]
        on = on[k:] + on[:k]
    nodes = []
    P, pending = pts[0], None
    for q, q_on in list(zip(pts[1:], on[1:])) + [(pts[0], True)]:
        if q_on:
            nodes.append([P, pending])
            P, pending = q, None
        elif pending is None:
            pending = q
        else:
            mid = ((pending[0] + q[0]) / 2, (pending[1] + q[1]) / 2)
            nodes.append([P, pending])
            P, pending = mid, q
    # drop zero-length line segments
    out = []
    for i, (P, C) in enumerate(nodes):
        Pn = nodes[(i + 1) % len(nodes)][0]
        if C is None and abs(P[0] - Pn[0]) < 0.01 and abs(P[1] - Pn[1]) < 0.01:
            continue
        out.append([P, C])
    return out


def nodes_to_tt(nodes):
    coords, flags = [], []
    for P, C in nodes:
        coords.append((round(P[0]), round(P[1])))
        flags.append(1)
        if C is not None:
            coords.append((round(C[0]), round(C[1])))
            flags.append(0)
    return coords, flags


def flatten(nodes, steps=8):
    out = []
    n = len(nodes)
    for i, (P, C) in enumerate(nodes):
        out.append(P)
        if C is not None:
            Q = nodes[(i + 1) % n][0]
            for k in range(1, steps):
                t = k / steps
                a, b, c = (1 - t) ** 2, 2 * (1 - t) * t, t * t
                out.append((a * P[0] + b * C[0] + c * Q[0],
                            a * P[1] + b * C[1] + c * Q[1]))
    return out


# ---------------------------------------------------------------- rasteriser
class Raster:
    """Non-zero winding rasteriser on a pixel grid of PX font units."""

    def __init__(self, contours, margin=700.0, grid=None):
        if grid is None:
            pts = [p for c in contours for p in flatten(c)]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            self.ox = math.floor((min(xs) - margin) / PX) * PX
            self.oy = math.floor((min(ys) - margin) / PX) * PX
            self.W = int((max(xs) + margin - self.ox) / PX) + 2
            self.H = int((max(ys) + margin - self.oy) / PX) + 2
        else:
            self.ox, self.oy, self.W, self.H = grid
        acc = np.zeros((self.H, self.W + 1), dtype=np.int32)
        for c in contours:
            poly = np.array(flatten(c), dtype=float)
            X = (poly[:, 0] - self.ox) / PX
            Y = (poly[:, 1] - self.oy) / PX
            X1, Y1 = np.roll(X, -1), np.roll(Y, -1)
            for x0, y0, x1, y1 in zip(X, Y, X1, Y1):
                if y0 == y1:
                    continue
                d = 1 if y1 > y0 else -1
                lo, hi = (y0, y1) if y1 > y0 else (y1, y0)
                r0 = int(math.ceil(lo - 0.5))
                r1 = int(math.ceil(hi - 0.5))  # exclusive
                if r1 <= r0:
                    continue
                rows = np.arange(r0, r1)
                xc = x0 + (rows + 0.5 - y0) * (x1 - x0) / (y1 - y0)
                cols = np.clip(np.ceil(xc - 0.5).astype(int), 0, self.W)
                np.add.at(acc, (rows, cols), d)
        self.mask = np.cumsum(acc, axis=1)[:, : self.W] != 0
        self._edt_in = None
        self._edt_out = None

    @property
    def edt_in(self):
        if self._edt_in is None:
            self._edt_in = ndimage.distance_transform_edt(self.mask)
        return self._edt_in

    @property
    def edt_out(self):
        if self._edt_out is None:
            self._edt_out = ndimage.distance_transform_edt(~self.mask)
        return self._edt_out

    def px(self, x, y):
        c = np.clip(np.rint((np.asarray(x) - self.ox) / PX - 0.5).astype(int), 0, self.W - 1)
        r = np.clip(np.rint((np.asarray(y) - self.oy) / PX - 0.5).astype(int), 0, self.H - 1)
        return r, c


# ------------------------------------------------------------------ geometry
def _unit(v):
    n = math.hypot(v[0], v[1])
    return (v[0] / n, v[1] / n) if n > 1e-9 else (0.0, 0.0)


def node_frames(nodes):
    """Per node: (t_in, t_out) unit tangents of the arriving / leaving segment."""
    n = len(nodes)
    frames = []
    for i in range(n):
        P, C = nodes[i]
        Pp, Cp = nodes[i - 1]
        Pn = nodes[(i + 1) % n][0]
        t_in = _unit((P[0] - Cp[0], P[1] - Cp[1]) if Cp is not None else (P[0] - Pp[0], P[1] - Pp[1]))
        t_out = _unit((C[0] - P[0], C[1] - P[1]) if C is not None else (Pn[0] - P[0], Pn[1] - P[1]))
        if t_in == (0.0, 0.0):
            t_in = t_out
        if t_out == (0.0, 0.0):
            t_out = t_in
        frames.append((t_in, t_out))
    return frames


def miter_vectors(frames, sign):
    """Unit-offset displacement per node (outward = left of travel * sign)."""
    out = []
    for t_in, t_out in frames:
        n_in = (-t_in[1] * sign, t_in[0] * sign)
        n_out = (-t_out[1] * sign, t_out[0] * sign)
        c = n_in[0] * n_out[0] + n_in[1] * n_out[1]
        s = (n_in[0] + n_out[0], n_in[1] + n_out[1])
        k = 1.0 + c
        if k < 1e-3:
            m = (n_in[0], n_in[1])
        else:
            m = (s[0] / k, s[1] / k)
        L = math.hypot(*m)
        if L > MITER_LIMIT:
            m = (m[0] * MITER_LIMIT / L, m[1] * MITER_LIMIT / L)
        out.append(m)
    return out


def contour_sign(nodes, raster):
    """+1 if the left of the travel direction is empty space, else -1."""
    frames = node_frames(nodes)
    votes = 0
    for (P, _), (t_in, t_out) in zip(nodes, frames):
        t = _unit((t_in[0] + t_out[0], t_in[1] + t_out[1]))
        if t == (0.0, 0.0):
            continue
        left = (P[0] - t[1] * 2.5 * PX, P[1] + t[0] * 2.5 * PX)
        right = (P[0] + t[1] * 2.5 * PX, P[1] - t[0] * 2.5 * PX)
        rl, cl = raster.px(*left)
        rr, cr = raster.px(*right)
        votes += int(not raster.mask[rl, cl]) - int(not raster.mask[rr, cr])
    return 1 if votes >= 0 else -1


class Boundary:
    """Dense boundary samples (+ outward normals) of all contours of a glyph."""

    def __init__(self, contours, signs):
        pts, nrm = [], []
        for nodes, sg in zip(contours, signs):
            n = len(nodes)
            for i, (P, C) in enumerate(nodes):
                Q = nodes[(i + 1) % n][0]
                if C is None:
                    L = math.hypot(Q[0] - P[0], Q[1] - P[1])
                    k = max(1, int(L / 16))
                    t = _unit((Q[0] - P[0], Q[1] - P[1]))
                    for j in range(k):
                        f = j / k
                        pts.append((P[0] + (Q[0] - P[0]) * f, P[1] + (Q[1] - P[1]) * f))
                        nrm.append((-t[1] * sg, t[0] * sg))
                else:
                    for j in range(12):
                        f = j / 12
                        a, b, c = (1 - f) ** 2, 2 * (1 - f) * f, f * f
                        pts.append((a * P[0] + b * C[0] + c * Q[0], a * P[1] + b * C[1] + c * Q[1]))
                        dv = (2 * (1 - f) * (C[0] - P[0]) + 2 * f * (Q[0] - C[0]),
                              2 * (1 - f) * (C[1] - P[1]) + 2 * f * (Q[1] - C[1]))
                        t = _unit(dv)
                        nrm.append((-t[1] * sg, t[0] * sg))
        self.pts = np.array(pts)
        self.nrm = np.array(nrm)
        self.tree = cKDTree(self.pts)

    def clearance(self, P, nv, radius=500.0):
        """Distance from P to the nearest boundary that faces P across free
        space in the direction nv (isotropic, so corners 'see' neighbours)."""
        idx = self.tree.query_ball_point(P, radius)
        if not idx:
            return 1e9
        v = self.pts[idx] - np.array(P)
        dist = np.hypot(v[:, 0], v[:, 1])
        ok = dist > 1e-6
        ahead = (v @ np.array(nv)) > 0.2 * dist
        faces = -(v * self.nrm[idx]).sum(axis=1) > 0.2 * dist
        sel = ok & ahead & faces
        return float(dist[sel].min()) if sel.any() else 1e9


def measure(nodes, sign, raster, boundary):
    """Local stroke thickness (inward ray + EDT) and free clearance per node."""
    frames = node_frames(nodes)
    mv = miter_vectors(frames, sign)
    steps = np.arange(1, 160)            # up to ~640 units
    th, gap = [], []
    for (P, _), m in zip(nodes, mv):
        nv = _unit(m)
        if nv == (0.0, 0.0):
            th.append(1e9)
            gap.append(1e9)
            continue
        xs = P[0] - nv[0] * steps * PX
        ys = P[1] - nv[1] * steps * PX
        r, c = raster.px(xs, ys)
        inside = raster.mask[r, c]
        stop = np.argmin(inside) if not inside.all() else len(inside)
        stop = max(stop, 1)
        th.append(2.0 * raster.edt_in[r[:stop], c[:stop]].max() * PX)
        gap.append(boundary.clearance(P, nv))
    return np.array(th), np.array(gap)


def smooth_limit(lim):
    a = np.minimum(np.minimum(lim, np.roll(lim, 1)), np.roll(lim, -1))
    return (np.roll(a, 1) + 2 * a + np.roll(a, -1)) / 4.0


def _flatten_owned(nodes, steps=6):
    pts, owner = [], []
    n = len(nodes)
    for i, (P, C) in enumerate(nodes):
        pts.append(P)
        owner.append(i)
        if C is not None:
            Q = nodes[(i + 1) % n][0]
            for k in range(1, steps):
                t = k / steps
                a, b, c = (1 - t) ** 2, 2 * (1 - t) * t, t * t
                pts.append((a * P[0] + b * C[0] + c * Q[0], a * P[1] + b * C[1] + c * Q[1]))
                owner.append(i)
    return np.array(pts), np.array(owner)


def self_crossings(nodes):
    """Set of {node_a, node_b} pairs whose segments cross each other."""
    pts, owner = _flatten_owned(nodes)
    n = len(pts)
    B = np.roll(pts, -1, axis=0)
    d = B - pts
    out = set()
    idx = np.arange(n)
    for i in range(n):
        p, r = pts[i], d[i]
        rxs = r[0] * d[:, 1] - r[1] * d[:, 0]
        qp = pts - p
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (qp[:, 0] * d[:, 1] - qp[:, 1] * d[:, 0]) / rxs
            u = (qp[:, 0] * r[1] - qp[:, 1] * r[0]) / rxs
        hit = (rxs != 0) & (t > 1e-6) & (t < 1 - 1e-6) & (u > 1e-6) & (u < 1 - 1e-6)
        hit &= ~((idx == i) | (idx == (i + 1) % n) | (idx == (i - 1) % n))
        for j in np.nonzero(hit)[0]:
            a, b = int(owner[i]), int(owner[j])
            out.add((min(a, b), max(a, b)))
    return out


def _displace(nodes, mv, di):
    n = len(nodes)
    newP = [(nodes[i][0][0] + mv[i][0] * di[i], nodes[i][0][1] + mv[i][1] * di[i]) for i in range(n)]
    out = []
    for i in range(n):
        P, C = nodes[i]
        j = (i + 1) % n
        if C is None:
            out.append([newP[i], None])
            continue
        Q = nodes[j][0]
        alt = (C[0] + ((newP[i][0] - P[0]) + (newP[j][0] - Q[0])) / 2,
               C[1] + ((newP[i][1] - P[1]) + (newP[j][1] - Q[1])) / 2)
        a = (C[0] - P[0], C[1] - P[1])
        b = (Q[0] - C[0], Q[1] - C[1])
        cr = a[0] * b[1] - a[1] * b[0]
        la, lb = math.hypot(*a), math.hypot(*b)
        newC = alt
        if la > 1e-6 and lb > 1e-6 and abs(cr) / (la * lb) > 0.02:
            w = (newP[j][0] - newP[i][0], newP[j][1] - newP[i][1])
            u = (w[0] * b[1] - w[1] * b[0]) / cr
            v = (w[0] * a[1] - w[1] * a[0]) / cr
            cand = (newP[i][0] + u * a[0], newP[i][1] + u * a[1])
            shift = max(abs(newP[i][0] - P[0]), abs(newP[i][1] - P[1]),
                        abs(newP[j][0] - Q[0]), abs(newP[j][1] - Q[1]))
            far = math.hypot(cand[0] - alt[0], cand[1] - alt[1])
            if u > 0 and v < 1 and far < 2.0 * shift + 0.25 * (la + lb):
                newC = cand
        out.append([newP[i], newC])
    return out


def offset_contour(nodes, sign, d, th, gap):
    """Return a new node list moved by (up to) d along the outline normal.

    Where the offset would fold over itself (a loop appears that the Regular
    outline did not have) the displacement of the nodes on the loop is reduced
    step by step.  No node is ever added or removed, so all masters keep the
    same point structure."""
    n = len(nodes)
    frames = node_frames(nodes)
    mv = miter_vectors(frames, sign)
    mlen = np.maximum(1.0, np.array([math.hypot(*m) for m in mv]))
    if d >= 0:
        lim = np.minimum(K_BLACK * th, GAP_FACTOR * gap / mlen)
    else:
        lim = K_THIN * th
    mag = np.minimum(abs(d), smooth_limit(lim))
    base = self_crossings(nodes)
    scale = np.ones(n)
    out = None
    for _ in range(30):
        out = _displace(nodes, mv, np.sign(d) * mag * scale)
        new = self_crossings(out) - base
        if not new:
            break
        for a, b in new:
            arc = (b - a) % n if (b - a) % n <= (a - b) % n else (a - b) % n
            lo = a if (b - a) % n <= (a - b) % n else b
            for k in range(-1, arc + 2):
                scale[(lo + k) % n] *= 0.6
        scale = smooth_limit(scale)
    return out


# --------------------------------------------------------------- font access
def read_outlines(font):
    glyf = font["glyf"]
    res = {}
    for name in font.getGlyphOrder():
        g = glyf[name]
        if g.numberOfContours <= 0:
            res[name] = []
            continue
        if g.isComposite():
            raise SystemExit(f"composite glyph {name} not supported")
        coords, ends, flags = g.getCoordinates(glyf)
        contours, s = [], 0
        for e in ends:
            pts = [tuple(coords[i]) for i in range(s, e + 1)]
            on = [bool(flags[i] & 1) for i in range(s, e + 1)]
            nodes = tt_to_nodes(pts, on)
            if len(nodes) >= 2:
                contours.append(nodes)
            s = e + 1
        res[name] = contours
    return res


def make_glyph(contours, dx=0):
    g = Glyph()
    coords, ends, flags = [], [], []
    for nodes in contours:
        c, f = nodes_to_tt(nodes)
        coords.extend((x + dx, y) for x, y in c)
        flags.extend(f)
        ends.append(len(coords) - 1)
    if not coords:
        g.numberOfContours = 0
        return g
    g.numberOfContours = len(ends)
    g.coordinates = GlyphCoordinates(coords)
    g.endPtsOfContours = ends
    g.flags = bytearray(flags)
    g.program = Program()
    g.program.fromBytecode(b"")
    return g


def strip_hinting(font):
    for t in ("fpgm", "prep", "cvt ", "gasp", "hdmx", "LTSH", "VDMX"):
        if t in font:
            del font[t]
    glyf = font["glyf"]
    for name in font.getGlyphOrder():
        g = glyf[name]
        if g.numberOfContours > 0:
            g.program = Program()
            g.program.fromBytecode(b"")
    font["gasp"] = newTable("gasp")
    font["gasp"].version = 1
    font["gasp"].gaspRange = {0xFFFF: 0x000A}   # gray smoothing, symmetric


def finish_metrics(font):
    glyf, hmtx = font["glyf"], font["hmtx"]
    xmin = ymin = 10 ** 6
    xmax = ymax = -10 ** 6
    for name in font.getGlyphOrder():
        g = glyf[name]
        if g.numberOfContours > 0:
            g.recalcBounds(glyf)
            xmin, ymin = min(xmin, g.xMin), min(ymin, g.yMin)
            xmax, ymax = max(xmax, g.xMax), max(ymax, g.yMax)
            hmtx[name] = (hmtx[name][0], g.xMin)
        else:
            hmtx[name] = (hmtx[name][0], 0)
    head = font["head"]
    head.xMin, head.yMin, head.xMax, head.yMax = xmin, ymin, xmax, ymax
    font["hhea"].advanceWidthMax = max(a for a, _ in hmtx.metrics.values())
    font["hhea"].minLeftSideBearing = min(l for _, l in hmtx.metrics.values())
    font["maxp"].maxZones = 1
    font["maxp"].maxTwilightPoints = 0
    font["maxp"].maxStorage = 0
    font["maxp"].maxFunctionDefs = 0
    font["maxp"].maxInstructionDefs = 0
    font["maxp"].maxStackElements = 0
    font["maxp"].maxSizeOfInstructions = 0


def upgrade_os2(font):
    """Regular ships OS/2 v1; variable fonts need v2+ (xHeight/capHeight)."""
    os2 = font["OS/2"]
    if os2.version >= 4:
        return
    glyf = font["glyf"]
    os2.version = 4
    os2.sxHeight = glyf["x"].yMax if glyf["x"].numberOfContours else 0
    os2.sCapHeight = glyf["H"].yMax if glyf["H"].numberOfContours else 0
    os2.usDefaultChar = 0
    os2.usBreakChar = 32
    os2.usMaxContext = 3


def set_names(font, style, weight, ps_style=None):
    """Name table for one master / static instance (RIBBI-aware)."""
    ps_style = ps_style or style.replace(" ", "")
    ribbi = style in ("Regular", "Bold")
    fam1 = FAMILY if ribbi else f"{FAMILY} {style}"
    sub2 = style if ribbi else "Regular"
    full = f"{FAMILY} {style}"
    name = font["name"]
    keep = {0, 8, 9, 11, 13, 14}
    vals = {n.nameID: n.toUnicode() for n in name.names if n.platformID == 3 and n.nameID in keep}
    name.names = [n for n in name.names if n.nameID in keep]
    recs = {
        **vals,
        1: fam1, 2: sub2, 3: f"{FAMILY} {style}; Version {VERSION}; elhizazi1",
        4: full, 5: f"Version {VERSION}", 6: f"{PS_FAMILY}-{ps_style}",
        16: FAMILY, 17: style,
    }
    if ribbi:
        recs.pop(16), recs.pop(17)
    for nid, text in sorted(recs.items()):
        name.setName(text, nid, 3, 1, 0x409)
    os2, head = font["OS/2"], font["head"]
    os2.usWeightClass = weight
    sel = os2.fsSelection & ~(0b1100001)            # clear italic/bold/regular
    sel |= 0b100000 if style == "Bold" else (0b1000000 if style == "Regular" else 0)
    os2.fsSelection = sel
    head.macStyle = 1 if style == "Bold" else 0
    os2.panose.bWeight = {100: 2, 200: 3, 300: 4, 400: 5, 500: 6, 600: 7,
                          700: 8, 800: 9, 900: 10}[weight]


# ---------------------------------------------------------------- masters
def build_master(regular_path, d, style, weight, report=None):
    font = TTFont(regular_path)
    strip_hinting(font)
    glyf, hmtx = font["glyf"], font["hmtx"]
    outlines = read_outlines(TTFont(regular_path))
    for name in font.getGlyphOrder():
        contours = outlines[name]
        adv, _ = hmtx[name]
        if not contours:
            glyf[name] = make_glyph([])
            continue
        new = []
        if d == 0:
            new = contours
        else:
            raster = Raster(contours)
            signs = [contour_sign(nodes, raster) for nodes in contours]
            boundary = Boundary(contours, signs)
            for nodes, sign in zip(contours, signs):
                th, gap = measure(nodes, sign, raster, boundary)
                new.append(offset_contour(nodes, sign, d, th, gap))
        # Grow the glyph box by the amount its outline extremes actually moved
        # (clamps keep thin tips from moving the full offset).  Every join then
        # keeps exactly the overlap it has in Regular.
        def end_shift(old_cs, new_cs):
            """Median x-movement of the nodes in the last END_ZONE units at
            each side: robust against small spurs that move less than the
            stroke end where the join actually happens."""
            on = [p[0] for c in old_cs for p, _ in c]
            nn = [p[0] for c in new_cs for p, _ in c]
            lo, hi = min(on), max(on)
            L = [o - n_ for o, n_ in zip(on, nn) if o <= lo + END_ZONE]
            R = [n_ - o for o, n_ in zip(on, nn) if o >= hi - END_ZONE]
            return float(np.median(L)), float(np.median(R))
        left, right = end_shift(contours, new)
        glyf[name] = make_glyph(new, dx=round(left))
        bias = THIN_JOIN_BIAS * abs(d) if d < 0 else 0.0   # extra join overlap for thin weights
        hmtx[name] = (max(0, round(adv + left + right - bias)) if adv > 0 else adv, 0)
        if report is not None:
            report[name] = (contours, new)
    finish_metrics(font)
    upgrade_os2(font)
    set_names(font, style, weight)
    return font

# ------------------------------------------------------------- family build
def build_family():
    from fontTools.designspaceLib import (DesignSpaceDocument, AxisDescriptor,
                                          SourceDescriptor, InstanceDescriptor)
    from fontTools import varLib
    from fontTools.varLib import instancer

    mdir = ROOT / "sources/masters"
    for sub in ("fonts/ttf", "fonts/woff2", "fonts/variable"):
        (ROOT / sub).mkdir(parents=True, exist_ok=True)
    mdir.mkdir(parents=True, exist_ok=True)

    masters = {}
    for style, w, d in (("Thin", 100, D_THIN), ("Regular", 400, 0), ("Black", 900, D_BLACK)):
        print(f"master {style} (offset {d:+})")
        f = build_master(REGULAR, d, style, w)
        path = mdir / f"MaghribiAssile-{style}-master.ttf"
        f.save(path)
        masters[w] = path

    ds = DesignSpaceDocument()
    ax = AxisDescriptor()
    ax.name, ax.tag, ax.minimum, ax.default, ax.maximum = "Weight", "wght", 100, 400, 900
    ax.map = [(100, 100), (400, 400), (900, 900)]
    ds.addAxis(ax)
    for w, path in masters.items():
        sd = SourceDescriptor()
        sd.path = str(path)
        sd.familyName = FAMILY
        sd.location = {"Weight": w}
        ds.addSource(sd)
    for w, style in WEIGHTS:
        inst = InstanceDescriptor()
        inst.familyName, inst.styleName = FAMILY, style
        inst.location = {"Weight": w}
        ds.addInstance(inst)
    ds.write(ROOT / "sources/MaghribiAssile.designspace")

    vf, _, _ = varLib.build(ds, master_finder=lambda p: p)
    vf = vf if isinstance(vf, TTFont) else vf
    set_names(vf, "Regular", 400)
    name = vf["name"]
    name.setName(f"{FAMILY} Variable", 4, 3, 1, 0x409)
    name.setName(f"{PS_FAMILY}-Regular", 6, 3, 1, 0x409)
    vf_path = ROOT / "fonts/variable/MaghribiAssile[wght].ttf"
    vf.save(vf_path)
    save_woff2(vf_path, ROOT / "fonts/variable/MaghribiAssile[wght].woff2")

    for w, style in WEIGHTS:
        if w == 400:
            continue   # the original Regular is shipped untouched
        inst = instancer.instantiateVariableFont(TTFont(vf_path), {"wght": w}, inplace=False)
        for t in ("fvar", "gvar", "avar", "STAT", "HVAR", "MVAR"):
            if t in inst:
                del inst[t]
        set_names(inst, style, w)
        finish_metrics(inst)
        out = ROOT / f"fonts/ttf/{PS_FAMILY}-{style}.ttf"
        inst.save(out)
        save_woff2(out, ROOT / f"fonts/woff2/{PS_FAMILY}-{style}.woff2")
        print("wrote", out.name)


def save_woff2(src, dst):
    try:
        import brotli  # noqa: F401
    except ImportError:
        print(f"  skipped {Path(dst).name}: install brotli (pip install -r requirements.txt)")
        return
    f = TTFont(src)
    f.flavor = "woff2"
    f.save(dst)


if __name__ == "__main__":
    build_family()
