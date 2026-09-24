#!/usr/bin/env python3
"""Montage par couche + camera qui recule au fur et a mesure que le modele monte.

Le zoom casse l'astuce des tampons incrementaux de layers.py : des que l'echelle
change, tous les sommets doivent etre reprojetes. On reprend donc le schema
fonctionnel de walle3.py -- l'etat a l'image t ne depend que de t -- et on
parallelise par tranches d'images sur des processus independants.

Deux differences avec walle3 :

* Le cadrage est calcule en ESPACE ECRAN sur un nuage de points (les 8 coins de
  la boite de chaque piece), pas sur la boite englobante du modele. Sous une vue
  inclinee, la profondeur du socle gonfle la bbox bien au-dela de la silhouette
  reelle : cadrer sur la bbox laissait une statue de Mario a 62 % de la hauteur
  d'image au lieu de 90 %. Le centre camera est ensuite recale pour centrer exactement le nuage.

* Le decoupage en tranches est pondere par le cout : une image de la couche 70
  rasterise 20 fois plus de briques qu'une image de la couche 3. A tranches
  egales, le dernier worker travaillerait trois fois plus longtemps que le premier.

  layers_zoom.py <model.ldr> --parallel 6 --out film.mp4
  layers_zoom.py <model.ldr> --range A B --out segment.mp4     (worker interne)
  layers_zoom.py <model.ldr> --still N                         (image de controle)
  layers_zoom.py <model.ldr> --parallel 5 --endyaw 270         (tour arrete sur 270 deg)
"""
import sys, os, math, time, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine
from engine import W, H, BG, ZCLEAR, HDR, PITCH, draw, project, progress

FALL, SETTLE, SPIN = 10, 2, 100
FPL = FALL + SETTLE               # images par couche
DROPH = 1500.0                    # hauteur de chute, en LDU
WAVE = 0.55                       # etalement du depart de chute sur la rangee
DRIFT = 0.30                      # tours de camera pendant le montage
MINFRAC = 0.30                    # cadrage minimal, en fraction de la hauteur totale
HEAD = 0.20                       # ciel au-dessus du sommet construit
MARGIN, MARGIN_ALL = 0.88, 0.92
EASE = 20                         # images de transition vers le cadrage tout-yaw
ENDYAW = None                     # --endyaw DEG : yaw d'arrivee du tour final
HOLD = 15                         # images d'arret sur cette vue, une fois arrive


def layer_sizes(path):
    """Nombre de lignes `1` entre chaque `0 layer` -- l'ordre des placements
    aplatis suit l'ordre du fichier (modele unique, pas de sous-fichier)."""
    sizes, cur = [], None
    for ln in open(path, errors='replace'):
        s = ln.strip()
        if s.lower().startswith('0 layer'):
            sizes.append(0)
            cur = len(sizes) - 1
        elif s.startswith('1 ') and cur is not None:
            sizes[cur] += 1
    return [n for n in sizes if n]


# ------------------------------------------------------------------- cadrage

def frame_fit(pts, upto, yaw, margin, min_sy):
    """Cadre pts[:upto] pour CE yaw. Renvoie (cam, scale).

    Les extremes sont pris en espace ecran, puis le centre camera est resolu pour
    les ramener au centre de l'image. Les deux corrections sont orthogonales :
      cx = mx*cos(yaw), cz = -mx*sin(yaw)  annule le decalage horizontal sans
      toucher a la profondeur ; cy = my/cos(pitch) annule le decalage vertical.
    """
    cp, sp = math.cos(PITCH), math.sin(PITCH)
    cyw, syw = math.cos(yaw), math.sin(yaw)
    xlo = ylo = 1e18
    xhi = yhi = -1e18
    for i in range(upto):
        x, y, z = pts[i]
        sx = x * cyw - z * syw
        sy = y * cp + (x * syw + z * cyw) * sp
        if sx < xlo: xlo = sx
        if sx > xhi: xhi = sx
        if sy < ylo: ylo = sy
        if sy > yhi: yhi = sy
    yhi += HEAD * (yhi - ylo)                      # ciel : on voit arriver les briques
    span = max(yhi - ylo, min_sy)
    mx, my = (xlo + xhi) * 0.5, (ylo + yhi) * 0.5
    ex = max((xhi - xlo) * 0.5, 1e-6)
    ey = max(span * 0.5, 1e-6)
    return (mx * cyw, my / cp, -mx * syw), min(W * margin / (2 * ex), H * margin / (2 * ey))


def frame_fit_spin(pts, margin):
    """Cadrage valable pour TOUS les yaws : le modele tourne autour de l'axe
    vertical passant par le centre X/Z du nuage. Sur un tour complet le |sx| max
    d'un point vaut son rayon, et son sy oscille dans y*cp +/- r*sp -- une seule
    passe suffit, pas besoin d'echantillonner les angles."""
    cp, sp = math.cos(PITCH), math.sin(PITCH)
    ax0 = (min(p[0] for p in pts) + max(p[0] for p in pts)) * 0.5
    az0 = (min(p[2] for p in pts) + max(p[2] for p in pts)) * 0.5
    ex, ylo, yhi = 1e-6, 1e18, -1e18
    for (x, y, z) in pts:
        ax, az = x - ax0, z - az0
        r = math.hypot(ax, az)
        if r > ex: ex = r
        a = y * cp - r * sp
        b = y * cp + r * sp
        if a < ylo: ylo = a
        if b > yhi: yhi = b
    my = (ylo + yhi) * 0.5
    ey = max((yhi - ylo) * 0.5, 1e-6)
    return (ax0, my / cp, az0), min(W * margin / (2 * ex), H * margin / (2 * ey))


# ------------------------------------------------------------------ geometrie

def geometry(path):
    """Pieces, decoupage en couches, et nuage de cadrage (8 coins par piece)."""
    pieces, lo, hi = engine.load(path)
    sizes = layer_sizes(path)
    if sum(sizes) != len(pieces):
        sizes = [len(pieces)]

    pts = []
    for tris in pieces:
        bl = [1e18] * 3
        bh = [-1e18] * 3
        for (v, _) in tris:
            for p in v:
                for k in range(3):
                    if p[k] < bl[k]: bl[k] = p[k]
                    if p[k] > bh[k]: bh[k] = p[k]
        if bl[0] > bh[0]:                          # piece vide (geometrie absente)
            bl = bh = [0.0, 0.0, 0.0]
        for x in (bl[0], bh[0]):
            for y in (bl[1], bh[1]):
                for z in (bl[2], bh[2]):
                    pts.append((x, y, z))
    return pieces, sizes, pts, lo, hi


# --------------------------------------------------------------------- rendu

def state(f, nL, starts, pts, lo, hi):
    """(couche, k_dans_couche, yaw, cam, scale, avancement).  couche = -1 : tour final."""
    cp = math.cos(PITCH)
    min_sy = MINFRAC * (hi[1] - lo[1]) * cp
    NB = nL * FPL
    yaw_end = engine.YAW + 2 * math.pi * DRIFT

    if f < NB:
        li, k = f // FPL, f % FPL
        u = li + min(1.0, (k + 1) / FALL)
        yaw = engine.YAW + 2 * math.pi * DRIFT * (u / nL)
        # interpolation entre le cadrage de la couche i et celui de la couche i+1,
        # sinon le zoom avance par marches de 24 LDU toutes les 12 images
        i = min(nL - 1, int(u))
        j = min(nL - 1, i + 1)
        t = u - i
        if t < 0.0: t = 0.0
        elif t > 1.0: t = 1.0
        t = t * t * (3 - 2 * t)
        ca, sa = frame_fit(pts, starts[i + 1] * 8, yaw, MARGIN, min_sy)
        if j == i:
            return li, k, yaw, ca, sa, u / nL
        cb, sb = frame_fit(pts, starts[j + 1] * 8, yaw, MARGIN, min_sy)
        cam = tuple(ca[m] + (cb[m] - ca[m]) * t for m in range(3))
        return li, k, yaw, cam, sa + (sb - sa) * t, u / nL

    f2 = f - NB
    if ENDYAW is None:
        yaw = yaw_end + 2 * math.pi * f2 / SPIN
    else:
        # tour allonge de ce qu'il faut pour finir sur l'angle demande, puis arret
        # HOLD images dessus : les yeux du champignon ne se voient que d'un cote,
        # et une video qui s'arrete en plein mouvement ne les laisse pas lire.
        span = 2 * math.pi + (ENDYAW - yaw_end) % (2 * math.pi)
        n = max(SPIN - HOLD - 1, 1)
        yaw = yaw_end + span * min(f2, n) / n
    c0, s0 = frame_fit(pts, len(pts), yaw_end, MARGIN, min_sy)   # derniere image du montage
    c1, s1 = frame_fit_spin(pts, MARGIN_ALL)
    e = min(1.0, f2 / EASE)
    e = e * e * (3 - 2 * e)
    cam = tuple(c0[m] + (c1[m] - c0[m]) * e for m in range(3))
    return -1, 0, yaw, cam, s0 + (s1 - s0) * e, 1.0


def compose(cb, zb, pieces, first, last, k, yaw, cam, scale):
    """Rend pieces[:last] ; celles de [first,last) tombent encore si k < FALL."""
    flats = project(pieces[:last], cam, scale, yaw)
    for j in range(first):
        draw(cb, zb, flats[j])
    if k >= FALL:
        for j in range(first, last):
            draw(cb, zb, flats[j])
        return
    cp, sp = math.cos(PITCH), math.sin(PITCH)
    xs = [flats[j][0][0] if flats[j] else W * 0.5 for j in range(first, last)]
    if not xs:
        return
    xmin = min(xs)
    xspan = (max(xs) - xmin) or 1.0
    for jj, j in enumerate(range(first, last)):
        ph = (xs[jj] - xmin) / xspan * WAVE            # retard de depart, en vague
        t = (k / FALL - ph) / (1.0 - WAVE)
        if t < 0.0: t = 0.0
        elif t > 1.0: t = 1.0
        off = DROPH * (1.0 - t) ** 3
        draw(cb, zb, flats[j], 0.0, -off * cp * scale, -off * sp)


def render_frame(pieces, starts, nL, pts, lo, hi, f):
    li, k, yaw, cam, scale, p = state(f, nL, starts, pts, lo, hi)
    cb, zb = bytearray(BG), ZCLEAR[:]
    n = len(pieces)
    if li < 0:
        compose(cb, zb, pieces, n, n, FALL, yaw, cam, scale)
    else:
        compose(cb, zb, pieces, starts[li], starts[li + 1], k, yaw, cam, scale)
    progress(cb, p)
    return cb


# ------------------------------------------------------- planning / parallele

FLOOR = 1800              # cout fixe par image, en equivalent-briques (cf. plus bas)


def frame_cost(sizes):
    """Cout relatif de chaque image : un plancher fixe + le nombre de briques.

    Le plancher n'est pas cosmetique. Mesure sur un premier rendu a 5 processus :
    2,35 s fixes + 0,00132 s par brique, soit l'equivalent de 1 777 briques --
    25 % du cout d'une image pleine. Sans lui, le worker qui recoit les couches
    basses se voit attribuer 451 images « bon marche » et finit bon dernier.

    La raison tient au zoom : comme le cadrage garde le modele plein cadre en
    permanence, le nombre de pixels a remplir ne varie quasiment pas d'une image
    a l'autre. Seul le travail par triangle (projection, mise en place des
    aretes) suit le nombre de briques. Un rendu a camera fixe, lui, n'aurait pas
    ce plancher : le modele y occupe d'abord trois pixels."""
    cost, acc = [], 0
    for cnt in sizes:
        acc += cnt
        cost.extend([FLOOR + acc] * FPL)
    cost.extend([FLOOR + acc] * SPIN)
    return cost


def cuts(cost, nproc):
    """Bornes de tranches a cout ~egal, jamais vides."""
    n = len(cost)
    nproc = max(1, min(nproc, n))
    tot = float(sum(cost)) or 1.0
    b, acc, k = [0], 0.0, 1
    for i in range(n):
        acc += cost[i]
        if k < nproc and acc >= tot * k / nproc and (n - i - 1) >= (nproc - k):
            b.append(i + 1)
            k += 1
    while len(b) < nproc:
        b.append(b[-1] + 1)
    b.append(n)
    return b


def offsets(sizes):
    s = [0]
    for c in sizes:
        s.append(s[-1] + c)
    return s


def render_range(path, a, b, outfile):
    pieces, sizes, pts, lo, hi = geometry(path)
    nL = len(sizes)
    starts = offsets(sizes)
    b = min(b, nL * FPL + SPIN)

    ff = subprocess.Popen(
        ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'image2pipe', '-vcodec', 'ppm',
         '-framerate', '30', '-i', '-', '-vf', 'scale=960:-2:flags=lanczos,format=yuv420p',
         '-c:v', 'libx264', '-crf', '20', '-preset', 'veryfast', '-threads', '2', outfile],
        stdin=subprocess.PIPE)
    out, t0 = ff.stdin, time.time()
    for f in range(a, b):
        out.write(HDR)
        out.write(bytes(render_frame(pieces, starts, nL, pts, lo, hi, f)))
        if (f - a) % 10 == 0:
            print('[%s] %d/%d  %.0fs' % (os.path.basename(outfile), f - a, b - a,
                                         time.time() - t0), file=sys.stderr, flush=True)
    out.close()
    ff.wait()


def main():
    global ENDYAW
    path = sys.argv[1]
    if '--endyaw' in sys.argv:
        ENDYAW = math.radians(float(sys.argv[sys.argv.index('--endyaw') + 1]))

    if '--still' in sys.argv:
        f = int(sys.argv[sys.argv.index('--still') + 1])
        pieces, sizes, pts, lo, hi = geometry(path)
        cb = render_frame(pieces, offsets(sizes), len(sizes), pts, lo, hi, f)
        sys.stdout.buffer.write(HDR)
        sys.stdout.buffer.write(bytes(cb))
        return

    if '--range' in sys.argv:
        k = sys.argv.index('--range')
        render_range(path, int(sys.argv[k + 1]), int(sys.argv[k + 2]),
                     sys.argv[sys.argv.index('--out') + 1])
        return

    # le parent ne charge PAS la geometrie : le planning ne depend que du decoupage
    # en couches, et 2,2 Go de plus a cote de N workers ne tiendraient pas en RAM
    nproc = int(sys.argv[sys.argv.index('--parallel') + 1]) if '--parallel' in sys.argv else 6
    ey = ['--endyaw', sys.argv[sys.argv.index('--endyaw') + 1]] if '--endyaw' in sys.argv else []
    final = sys.argv[sys.argv.index('--out') + 1] if '--out' in sys.argv else 'layers_zoom.mp4'
    sizes = layer_sizes(path)
    cost = frame_cost(sizes)
    T = len(cost)
    b = cuts(cost, nproc)
    print('%d pieces, %d couches, %d images (%.0f s), %d processus'
          % (sum(sizes), len(sizes), T, T / 30, nproc), file=sys.stderr)
    print('tranches : %s' % ' '.join('%d-%d' % (b[k], b[k + 1]) for k in range(nproc)),
          file=sys.stderr)

    seg, procs = [], []
    for k in range(nproc):
        s = 'm2_%02d.mp4' % k
        seg.append(s)
        procs.append(subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), path,
             '--range', str(b[k]), str(b[k + 1]), '--out', s] + ey,
            stderr=open('m2_%02d.log' % k, 'w')))
    t0 = time.time()
    for pr in procs:
        pr.wait()
    print('segments rendus en %.0fs' % (time.time() - t0), file=sys.stderr)

    with open('m2_concat.txt', 'w') as f:
        for s in seg:
            f.write("file '%s'\n" % s)
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0',
                    '-i', 'm2_concat.txt', '-c', 'copy', final], check=True)
    print('=> %s  (%.0fs total)' % (final, time.time() - t0), file=sys.stderr)


if __name__ == '__main__':          # sweep.py reimporte ce module
    main()
