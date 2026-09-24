#!/usr/bin/env python3
"""Rendu d'un modele LDraw reel (z-buffer) -> flux PPM sur stdout.

Camera fixe pendant le montage : les sommets projetes sont precalcules une fois,
et la chute d'une piece n'est qu'un decalage constant (dy, dz) en espace ecran.
Chaque image de montage ne rasterise donc QUE la piece qui tombe, par-dessus une
copie des tampons deja consolides. Seul le tour final reprojette tout.
"""
import sys, os, math, time
from array import array

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ldrawlib import Lib, load_mpd, placements

W, H = 1440, 960
YAW, PITCH = math.radians(35), math.radians(24)
LIGHT = (-0.42, 0.80, 0.43)
FAR = 1e30
DROP, SETTLE, SPIN = 3, 1, 90
DROPH = 900.0                     # hauteur de chute, en LDU

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib', 'ldraw')


def load(mpd_path):
    lib = Lib(ROOT)
    files, main = load_mpd(mpd_path)
    pl = placements(files, main)

    # geometrie monde : LDraw a Y vers le bas, on remet Y vers le haut
    pieces, lo, hi = [], [1e18] * 3, [-1e18] * 3
    lx, ly, lz = LIGHT
    for (ref, col, M) in pl:
        a, b, c, d, e, f, g, h, i, tx, ty, tz = M
        base = lib.rgb(col if col != 16 else 7)
        tris = []
        for (p1, p2, p3, tc) in lib.geom(ref):
            rgb = base if tc == 16 else lib.rgb(tc)
            v = []
            for p in (p1, p2, p3):
                px, py, pz = p
                v.append((a * px + b * py + c * pz + tx,
                          -(d * px + e * py + f * pz + ty),
                          g * px + h * py + i * pz + tz))
            (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = v
            ux, uy, uz = x1 - x0, y1 - y0, z1 - z0
            vx, vy, vz = x2 - x0, y2 - y0, z2 - z0
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            nl = math.sqrt(nx * nx + ny * ny + nz * nz)
            if nl < 1e-9:
                continue                                   # triangle degenere
            k = 0.30 + 0.70 * abs((nx * lx + ny * ly + nz * lz) / nl)
            tris.append((v, bytes(min(255, int(ch * k)) for ch in rgb)))
            for p in v:
                for j in range(3):
                    if p[j] < lo[j]: lo[j] = p[j]
                    if p[j] > hi[j]: hi[j] = p[j]
        pieces.append(tris)
    return pieces, lo, hi


def make_cam(lo, hi, margin=0.90):
    cx, cy, cz = ((lo[j] + hi[j]) / 2 for j in range(3))
    cp, sp = math.cos(PITCH), math.sin(PITCH)
    ex = ey = 0.0
    for yaw in [YAW] + [2 * math.pi * k / 16 for k in range(16)]:
        cyw, syw = math.cos(yaw), math.sin(yaw)
        for bx in (lo[0], hi[0]):
            for by in (lo[1], hi[1]):
                for bz in (lo[2], hi[2]):
                    ax, az = bx - cx, bz - cz
                    ex = max(ex, abs(ax * cyw - az * syw))
                    ey = max(ey, abs((by - cy) * cp + (ax * syw + az * cyw) * sp))
    return (cx, cy, cz), min(W * margin / (2 * ex), H * margin / (2 * ey))


def project(pieces, cam, scale, yaw):
    """Sommets -> (x_ecran, y_ecran, profondeur). Profondeur : plus petit = plus proche."""
    (cx, cy, cz) = cam
    cyw, syw = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(PITCH), math.sin(PITCH)
    ox, oy = W * 0.5, H * 0.5
    out = []
    for tris in pieces:
        flat = []
        for (v, colb) in tris:
            t = []
            for (x, y, z) in v:
                ax, ay, az = x - cx, y - cy, z - cz
                rx = ax * cyw - az * syw
                rz = ax * syw + az * cyw
                t.append(ox + rx * scale)
                t.append(oy - (ay * cp + rz * sp) * scale)
                t.append(-ay * sp + rz * cp)
            flat.append((t[0], t[1], t[2], t[3], t[4], t[5], t[6], t[7], t[8], colb))
        out.append(flat)
    return out


def draw(cbuf, zbuf, flat, dy=0.0, dz=0.0):
    """Rasterisation z-buffer, scanline, profondeur interpolee lineairement."""
    for (ax, ay, az, bx, by, bz, cx_, cy_, cz_, colb) in flat:
        ay += dy; by += dy; cy_ += dy
        az += dz; bz += dz; cz_ += dz
        if ay > by: ax, ay, az, bx, by, bz = bx, by, bz, ax, ay, az
        if by > cy_: bx, by, bz, cx_, cy_, cz_ = cx_, cy_, cz_, bx, by, bz
        if ay > by: ax, ay, az, bx, by, bz = bx, by, bz, ax, ay, az
        y0 = int(ay + 0.5)
        y1 = int(cy_ + 0.5)
        if y1 < 0 or y0 > H:
            continue
        if y0 < 0: y0 = 0
        if y1 > H - 1: y1 = H - 1
        dac = cy_ - ay
        dab = by - ay
        dbc = cy_ - by
        for y in range(y0, y1 + 1):
            yc = y + 0.5
            # les interpolants sont bornes : sur un triangle quasi horizontal
            # un denominateur minuscule projetterait l'arete hors ecran
            t = (yc - ay) / dac if dac > 1e-9 else 0.0
            if t < 0.0: t = 0.0
            elif t > 1.0: t = 1.0
            xl = ax + t * (cx_ - ax); zl = az + t * (cz_ - az)
            if yc < by:
                t2 = (yc - ay) / dab if dab > 1e-9 else 0.0
                if t2 < 0.0: t2 = 0.0
                elif t2 > 1.0: t2 = 1.0
                xr = ax + t2 * (bx - ax); zr = az + t2 * (bz - az)
            else:
                t2 = (yc - by) / dbc if dbc > 1e-9 else 0.0
                if t2 < 0.0: t2 = 0.0
                elif t2 > 1.0: t2 = 1.0
                xr = bx + t2 * (cx_ - bx); zr = bz + t2 * (cz_ - bz)
            if xl > xr:
                xl, xr, zl, zr = xr, xl, zr, zl
            i0 = int(xl + 0.5)
            i1 = int(xr + 0.5)
            if i1 < 0 or i0 > W - 1:
                continue
            span = xr - xl
            slope = (zr - zl) / span if span > 1e-9 else 0.0
            if i0 < 0: i0 = 0
            if i1 > W - 1: i1 = W - 1
            z = zl + (i0 + 0.5 - xl) * slope
            idx = y * W + i0
            for _ in range(i1 - i0 + 1):
                if z < zbuf[idx]:
                    zbuf[idx] = z
                    o = idx * 3
                    cbuf[o:o + 3] = colb
                z += slope
                idx += 1


BG = bytearray(W * H * 3)
for _y in range(H):
    _t = _y / H
    BG[_y * W * 3:(_y + 1) * W * 3] = bytes((int(36 + 26 * _t), int(42 + 30 * _t), int(52 + 34 * _t))) * W
ZCLEAR = array('f', [FAR]) * (W * H)
HDR = b'P6\n%d %d\n255\n' % (W, H)


def progress(cbuf, frac):
    x0, y0, w, h = int(W * 0.25), H - 46, int(W * 0.5), 8
    for yy in range(y0, y0 + h):
        o = (yy * W + x0) * 3
        cbuf[o:o + w * 3] = b'\x3a\x40\x48' * w
        n = int(w * frac)
        if n:
            cbuf[o:o + n * 3] = b'\xff\xd7\x3c' * n


def main():
    mpd = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else '--video'
    t0 = time.time()
    pieces, lo, hi = load(mpd)
    cam, scale = make_cam(lo, hi)
    ntri = sum(len(p) for p in pieces)
    print('%d pieces, %d triangles, echelle %.3f  (%.1fs)' % (len(pieces), ntri, scale, time.time() - t0),
          file=sys.stderr)

    out = sys.stdout.buffer
    if mode == '--still':
        flats = project(pieces, cam, scale, YAW)
        cbuf, zbuf = bytearray(BG), ZCLEAR[:]
        for f in flats:
            draw(cbuf, zbuf, f)
        progress(cbuf, 1.0)
        out.write(HDR); out.write(bytes(cbuf))
        print('still en %.1fs' % (time.time() - t0), file=sys.stderr)
        return

    # ---- montage : camera fixe, tampons incrementaux
    flats = project(pieces, cam, scale, YAW)
    cp, sp = math.cos(PITCH), math.sin(PITCH)
    cbuf, zbuf = bytearray(BG), ZCLEAR[:]
    n = len(flats)
    for i, f in enumerate(flats):
        for k in range(DROP + SETTLE):
            if k < DROP:
                t = k / DROP
                off = DROPH * (1 - t) ** 3
                fc, fz = bytearray(cbuf), zbuf[:]
                draw(fc, fz, f, -off * cp * scale, -off * sp)
                progress(fc, i / n)
                out.write(HDR); out.write(bytes(fc))
            else:
                draw(cbuf, zbuf, f)
                tmp = bytearray(cbuf)
                progress(tmp, (i + 1) / n)
                out.write(HDR); out.write(bytes(tmp))
        if i % 50 == 0:
            print('  piece %d/%d  %.0fs' % (i, n, time.time() - t0), file=sys.stderr)

    print('montage rendu en %.0fs, tour final...' % (time.time() - t0), file=sys.stderr)
    for s in range(SPIN):
        fl = project(pieces, cam, scale, YAW + 2 * math.pi * s / SPIN)
        cb, zb = bytearray(BG), ZCLEAR[:]
        for f in fl:
            draw(cb, zb, f)
        progress(cb, 1.0)
        out.write(HDR); out.write(bytes(cb))
        print('  tour %d/%d  %.0fs' % (s, SPIN, time.time() - t0), file=sys.stderr)


main()
