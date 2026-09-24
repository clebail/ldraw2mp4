#!/usr/bin/env python3
"""Montage LDraw accelere par couche : le modele se construit rangee par rangee.

Base sur walle.py -- camera fixe, tampons incrementaux. Chaque `0 layer N` du
.ldr est une rangee de briques ; on les fait tomber ensemble, avec un leger
decalage en vague sur X, puis on les consolide dans les tampons. Une image de
montage ne rasterise donc que la rangee courante par-dessus une copie des
tampons figes -- 5 270 briques mais ~70 par image. Tour 360 final en
reprojection complete.

  layers.py <model.ldr> --still | ffmpeg ... -frames:v 1 out.png     (controle)
  layers.py <model.ldr>         | ffmpeg ...                          (video)
  layers.py <model.ldr> --endyaw 270 | ffmpeg ...   (tour final arrete sur 270 deg)
"""
import sys, os, math, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine
from engine import W, H, BG, ZCLEAR, HDR, PITCH, draw, project, progress

FALL, SETTLE, SPIN = 10, 2, 100
DROPH = 1500.0           # hauteur de chute, en LDU
WAVE = 0.55              # etalement du depart de chute sur la rangee (fraction de FALL)
ENDYAW = None            # --endyaw DEG : yaw d'arrivee du tour final (None = tour pile)
HOLD = 15                # images d'arret sur cette vue, une fois arrive


def layer_sizes(path):
    """Nombre de lignes `1` entre chaque `0 layer` de <path>.

    Un modele balise par couche est un fichier unique sans sous-fichier : l'ordre des placements
    aplatis suit exactement l'ordre du fichier, donc un simple decoupage suffit.
    """
    sizes, cur = [], None
    for ln in open(path, errors='replace'):
        s = ln.strip()
        if s.lower().startswith('0 layer'):
            sizes.append(0)
            cur = len(sizes) - 1
        elif s.startswith('1 ') and cur is not None:
            sizes[cur] += 1
    return [n for n in sizes if n]


def spin_yaw(s):
    """Yaw de l'image s du tour final.

    Sans --endyaw : un tour pile, la derniere image revient sur la premiere.
    Avec : on allonge le tour de ce qu'il faut pour finir sur l'angle demande
    (ici la face du champignon, ses yeux ne se voient que d'un cote), puis on
    tient la vue HOLD images -- une video qui s'arrete en plein mouvement ne
    laisse pas le temps de lire le modele.
    """
    if ENDYAW is None:
        return engine.YAW + 2 * math.pi * s / SPIN
    span = 2 * math.pi + (ENDYAW - engine.YAW) % (2 * math.pi)
    n = max(SPIN - HOLD - 1, 1)
    return engine.YAW + span * min(s, n) / n


def main():
    global ENDYAW
    mpd = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else '--video'
    if '--endyaw' in sys.argv:
        ENDYAW = math.radians(float(sys.argv[sys.argv.index('--endyaw') + 1]))
        if mode == '--endyaw':
            mode = '--video'
    t0 = time.time()
    pieces, lo, hi = engine.load(mpd)
    cam, scale = engine.make_cam(lo, hi)
    sizes = layer_sizes(mpd)
    if sum(sizes) != len(pieces):
        print('  ! %d lignes de couche pour %d pieces -- une seule couche'
              % (sum(sizes), len(pieces)), file=sys.stderr)
        sizes = [len(pieces)]
    print('%d pieces, %d couches, %d triangles, echelle %.3f  (%.1fs)'
          % (len(pieces), len(sizes), sum(len(p) for p in pieces), scale, time.time() - t0),
          file=sys.stderr)

    flats = project(pieces, cam, scale, engine.YAW)
    cp, sp = math.cos(PITCH), math.sin(PITCH)
    out = sys.stdout.buffer

    if mode == '--still':
        cb, zb = bytearray(BG), ZCLEAR[:]
        for f in flats:
            draw(cb, zb, f)
        progress(cb, 1.0)
        out.write(HDR); out.write(bytes(cb))
        print('still en %.1fs' % (time.time() - t0), file=sys.stderr)
        return

    # ---- montage : camera fixe, une rangee a la fois, tampons incrementaux
    cbuf, zbuf = bytearray(BG), ZCLEAR[:]
    nL = len(sizes)
    base = 0
    for li, cnt in enumerate(sizes):
        rng = list(range(base, base + cnt))
        xs = [flats[j][0][0] if flats[j] else W * 0.5 for j in rng]
        xmin = min(xs); xspan = (max(xs) - xmin) or 1.0

        for k in range(FALL):
            fc, fz = bytearray(cbuf), zbuf[:]
            for jj, j in enumerate(rng):
                ph = (xs[jj] - xmin) / xspan * WAVE          # retard de depart en vague
                t = (k / FALL - ph) / (1.0 - WAVE)
                if t < 0.0: t = 0.0
                elif t > 1.0: t = 1.0
                off = DROPH * (1.0 - t) ** 3
                draw(fc, fz, flats[j], -off * cp * scale, -off * sp)
            progress(fc, li / nL)
            out.write(HDR); out.write(bytes(fc))

        for j in rng:                                        # consolidation de la rangee
            draw(cbuf, zbuf, flats[j])
        for _ in range(SETTLE):
            tmp = bytearray(cbuf)
            progress(tmp, (li + 1) / nL)
            out.write(HDR); out.write(bytes(tmp))

        base += cnt
        if li % 10 == 0:
            print('  couche %d/%d  %.0fs' % (li, nL, time.time() - t0), file=sys.stderr)

    print('montage rendu en %.0fs, tour final...' % (time.time() - t0), file=sys.stderr)
    last = None
    for s in range(SPIN):
        yaw = spin_yaw(s)
        if last is not None and yaw == prev_yaw:      # images d'arret : rien a rendre
            out.write(HDR); out.write(last)
            continue
        fl = project(pieces, cam, scale, yaw)
        cb, zb = bytearray(BG), ZCLEAR[:]
        for f in fl:
            draw(cb, zb, f)
        progress(cb, 1.0)
        last, prev_yaw = bytes(cb), yaw
        out.write(HDR); out.write(last)
        if s % 20 == 0:
            print('  tour %d/%d  %.0fs' % (s, SPIN, time.time() - t0), file=sys.stderr)


main()
