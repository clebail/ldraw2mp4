#!/usr/bin/env python3
"""Montage LDraw avec camera automatique.

Quand la piece a poser arrive dans le dos de la camera, on pivote vers elle
avant de la lacher. Les images de montage restent incrementales (memcpy + une
piece) ; seules les images de rotation reprojettent le modele deja assemble.
"""
import sys, os, math, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine
from engine import W, H, BG, ZCLEAR, HDR, PITCH, DROPH, draw, project, progress

DROP, SETTLE, SPIN = 3, 1, 90
ROT = 9                  # images par rotation
SLOTS = 12               # secteurs de visee
BACK = 0.12              # au-dela de ce rz (en fraction du rayon) la piece est dans le dos
MINR = 0.30              # en deca de ce rayon la direction radiale n'a pas de sens


def centroids(pieces, cam):
    cx, cy, cz = cam
    out = []
    for tris in pieces:
        n = sx = sz = 0
        for (v, _) in tris:
            for (x, y, z) in v:
                sx += x; sz += z; n += 1
        out.append((sx / n - cx, sz / n - cz) if n else (0.0, 0.0))
    return out


def main():
    mpd = sys.argv[1]
    t0 = time.time()
    pieces, lo, hi = engine.load(mpd)
    cam, scale = engine.make_cam(lo, hi)
    cent = centroids(pieces, cam)
    R = max(math.hypot(a, c) for (a, c) in cent) or 1.0
    n = len(pieces)
    print('%d pieces, %d triangles, R=%.0f' % (n, sum(len(p) for p in pieces), R), file=sys.stderr)

    out = sys.stdout.buffer
    cp, sp = math.cos(PITCH), math.sin(PITCH)
    yaw = engine.YAW
    flats = project(pieces, cam, scale, yaw)
    cbuf, zbuf = bytearray(BG), ZCLEAR[:]
    step = 2 * math.pi / SLOTS
    rots = 0

    for i in range(n):
        a, c = cent[i]
        r = math.hypot(a, c)
        if r > MINR * R and (a * math.sin(yaw) + c * math.cos(yaw)) > BACK * R:
            target = round(math.atan2(-a, -c) / step) * step
            delta = (target - yaw + math.pi) % (2 * math.pi) - math.pi
            if abs(delta) > 1e-3:
                rots += 1
                done = pieces[:i]
                cb = zb = None
                for f in range(1, ROT + 1):
                    t = f / ROT
                    y = yaw + delta * t * t * (3 - 2 * t)      # smoothstep
                    fl = project(done, cam, scale, y)
                    cb, zb = bytearray(BG), ZCLEAR[:]
                    for g in fl:
                        draw(cb, zb, g)
                    progress(cb, i / n)
                    out.write(HDR); out.write(bytes(cb))
                yaw += delta
                cbuf, zbuf = cb, zb                            # la derniere image devient l'etat consolide
                flats = project(pieces, cam, scale, yaw)

        f = flats[i]
        for k in range(DROP + SETTLE):
            if k < DROP:
                off = DROPH * (1 - k / DROP) ** 3
                fc, fz = bytearray(cbuf), zbuf[:]
                draw(fc, fz, f, -off * cp * scale, -off * sp)
                progress(fc, i / n)
                out.write(HDR); out.write(bytes(fc))
            else:
                draw(cbuf, zbuf, f)
                tmp = bytearray(cbuf)
                progress(tmp, (i + 1) / n)
                out.write(HDR); out.write(bytes(tmp))
        if i % 100 == 0:
            print('  piece %d/%d, %d rotations, %.0fs' % (i, n, rots, time.time() - t0), file=sys.stderr)

    print('montage fini : %d rotations, %.0fs' % (rots, time.time() - t0), file=sys.stderr)
    for s in range(SPIN):
        fl = project(pieces, cam, scale, yaw + 2 * math.pi * s / SPIN)
        cb, zb = bytearray(BG), ZCLEAR[:]
        for g in fl:
            draw(cb, zb, g)
        progress(cb, 1.0)
        out.write(HDR); out.write(bytes(cb))
    print('total %.0fs' % (time.time() - t0), file=sys.stderr)


main()
