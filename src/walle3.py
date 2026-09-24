#!/usr/bin/env python3
"""Montage LDraw : pieces en tas au depart, camera automatique, zoom progressif.

Toutes les pieces existent des la premiere image (eparpillees en tas autour de
l'aire de montage), donc plus rien n'est incremental : retirer une piece d'un tas
impose de reconstruire le z-buffer. En echange, l'etat a l'image t ne depend que
de t -- le rendu est donc purement fonctionnel, et parallelisable par tranches
d'images sur des processus totalement independants.

  walle3.py <model.mpd> --parallel 11 --out film.mp4
  walle3.py <model.mpd> --range A B --out segment.mp4      (worker interne)
"""
import sys, os, math, time, random, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine
from engine import W, H, BG, ZCLEAR, HDR, PITCH, draw, project, progress
from ldrawlib import load_mpd, group_sizes

FLY, SETTLE, ROT, SPIN = 6, 1, 12, 120   # cadencement : images par vol, par
                                         # temps mort, par rotation, par tour
SLOTS, BACK, MINR = 12, 0.12, 0.30
NPILES, PILE_R, PILE_SIG, PILE_H = 7, 1.50, 0.155, 0.20
DROP = 0.35               # --piles 0 : chute, en fraction du deja-construit
ARC = 0.55                # hauteur de l'arc de vol, en fraction du rayon
SEED = 20213
ZOOM_END = 0.85           # le zoom est termine a 85 % du montage
ZOOM = 'progress'         # 'progress' : de la boite modele+tas vers le modele,
                          # au prorata des pieces posees (historique).
                          # 'fit' : cadre la boite des pieces DEJA posees, comme
                          # layers_zoom -- le champ suit la construction au lieu de
                          # partir du decor.
ZOOM_MIN = 0.35           # plancher du cadrage 'fit', en fraction de la hauteur
HEAD = 0.20               # ciel au-dessus du construit, pour voir arriver la piece
MARGIN = 0.90
MODE = 'tas'              # 'tas' : le montage historique, tout en vrac au depart.
                          # 'notice' : chaque sous-ensemble se monte a l'ecart,
                          # avec son propre cadrage, puis vole a sa place.
STAGE_R = 1.25            # distance de l'aire de montage, en R
TRANSFER = 18             # images du vol d'un bloc termine
HOLD = 12                 # pause sur le modele apres qu'un bloc s'y est pose
# la camera tourne pour degager le point d'accroche de la piece qui arrive,
# comme schedule() en mode tas -- d'ou plus d'orbite automatique


def configure(av):
    """Reglages des tas, depuis la ligne de commande.

    Les memes options existent dans le portage C++ (`lego --piles ...`) : sans
    ca, changer un tas ici rendrait le portage invalidable, puisque la reference
    de parite ne saurait plus decrire la meme scene.

      --piles N      nombre d'amas, 0 pour aucun          (defaut 7)
      --drop F       avec --piles 0 : hauteur de chute, en
                     fraction de ce qui est deja construit  (defaut 0.35)
      --pile-r F     rayon du cercle des amas, en R        (defaut 1.50)
      --pile-sig F   dispersion dans un amas, en R         (defaut 0.155)
      --pile-h F     dispersion en hauteur, en R           (defaut 0.20)
      --zoom M       'progress' ou 'fit'                    (defaut progress)
      --zoom-min F   plancher du cadrage 'fit', en fraction du
                     cadrage final                         (defaut 0.35)
      --zoom-head F  ciel au-dessus du construit            (defaut 0.20)
      --mode M       'tas' ou 'notice'                      (defaut tas)
      --stage-r F    notice : distance de l'atelier, en R    (defaut 1.25)
      --transfer N   notice : images du vol d'un bloc        (defaut 18)
      --hold N       notice : pause sur le modele apres la pose (defaut 12)

    Cadencement, commun aux deux modes -- c'est ce qui fixe la duree du film :
      --fly N        images du vol d'une piece              (defaut 6)
      --settle N     temps mort apres la pose               (defaut 1)
      --rot N        images d'une rotation de camera        (defaut 12)
      --spin N       images du tour final                   (defaut 120)
    """
    global NPILES, PILE_R, PILE_SIG, PILE_H, DROP, ZOOM, ZOOM_MIN, HEAD
    global MODE, STAGE_R, TRANSFER, HOLD, FLY, SETTLE, ROT, SPIN

    def opt(name, cur, cast):
        return cast(av[av.index(name) + 1]) if name in av else cur

    NPILES = opt('--piles', NPILES, int)
    PILE_R = opt('--pile-r', PILE_R, float)
    PILE_SIG = opt('--pile-sig', PILE_SIG, float)
    PILE_H = opt('--pile-h', PILE_H, float)
    DROP = opt('--drop', DROP, float)
    MODE = opt('--mode', MODE, str)
    STAGE_R = opt('--stage-r', STAGE_R, float)
    TRANSFER = opt('--transfer', TRANSFER, int)
    HOLD = opt('--hold', HOLD, int)
    FLY = opt('--fly', FLY, int)
    SETTLE = opt('--settle', SETTLE, int)
    ROT = opt('--rot', ROT, int)
    SPIN = opt('--spin', SPIN, int)
    ZOOM = opt('--zoom', ZOOM, str)
    ZOOM_MIN = opt('--zoom-min', ZOOM_MIN, float)
    HEAD = opt('--zoom-head', HEAD, float)


PILE_OPTS = ('--piles', '--pile-r', '--pile-sig', '--pile-h', '--drop',
             '--zoom', '--zoom-min', '--zoom-head',
             '--mode', '--stage-r', '--transfer', '--hold',
             '--fly', '--settle', '--rot', '--spin')


def geometry(mpd):
    pieces, lo, hi = engine.load(mpd)
    cam, tight = engine.make_cam(lo, hi)
    cx, cy, cz = cam
    n = len(pieces)

    cent, miny, bbox = [], [], []
    for tris in pieces:
        sx = sz = 0.0; k = 0
        bl = [1e18] * 3; bh = [-1e18] * 3
        for (v, _) in tris:
            for pt in v:
                sx += pt[0]; sz += pt[2]; k += 1
                for j in range(3):
                    if pt[j] < bl[j]: bl[j] = pt[j]
                    if pt[j] > bh[j]: bh[j] = pt[j]
        cent.append((sx / k - cx, sz / k - cz) if k else (0.0, 0.0))
        miny.append(bl[1] if k else 0.0)
        bbox.append((bl, bh) if k else ([0.0] * 3, [0.0] * 3))
    R = max(math.hypot(a, c) for (a, c) in cent) or 1.0
    ground = lo[1]



    # boite englobante des pieces 0..k-1, pour le cadrage 'fit' et la hauteur de
    # chute. Elle ne fait que croitre, d'ou un dezoom monotone -- et elle ne
    # depend que de k, donc l'image reste fonction de son seul numero.
    pre = [[1e18] * 3 + [-1e18] * 3]
    for i in range(n):
        bl, bh = bbox[i]
        q = pre[-1]
        pre.append([min(q[j], bl[j]) for j in range(3)] + [max(q[3 + j], bh[j]) for j in range(3)])

    # tas : NPILES amas repartis autour du modele, tirage deterministe
    rng = random.Random(SEED)
    piles = [(PILE_R * R * math.cos(2 * math.pi * k / NPILES),
              PILE_R * R * math.sin(2 * math.pi * k / NPILES)) for k in range(NPILES)]
    scatter = []
    for i in range(n):
        if not NPILES:
            # sans tas : la piece tombe a la verticale de sa place, comme le
            # pilote v1. Aucun tirage consomme -- randrange(0) leverait d'ailleurs.
            #
            # La hauteur est proportionnelle a ce qui est DEJA construit, pas au
            # rayon du modele fini : avec --zoom fit la camera est serree sur les
            # premieres pieces, et une chute calee sur le modele entier les ferait
            # entrer par le haut du cadre, hors champ pendant la moitie du vol.
            q = pre[max(i, 1)]
            scatter.append((0.0, DROP * max(q[3] - q[0], q[4] - q[1], q[5] - q[2]), 0.0))
            continue
        px, pz = piles[rng.randrange(NPILES)]
        tx = px + rng.gauss(0, PILE_SIG * R)
        tz = pz + rng.gauss(0, PILE_SIG * R)
        scatter.append((tx - cent[i][0],
                        ground - miny[i] + abs(rng.gauss(0, PILE_H * R)),
                        tz - cent[i][1]))

    # boite elargie : modele + tas. Chaque piece compte avec SON bbox, pas celui
    # du modele entier, sinon l'echelle de depart est absurdement large.
    wlo = [lo[0], lo[1], lo[2]]; whi = [hi[0], hi[1], hi[2]]
    for i in range(n):
        ox, oy, oz = scatter[i]
        bl, bh = bbox[i]
        for j, o in enumerate((ox, oy, oz)):
            wlo[j] = min(wlo[j], bl[j] + o)
            whi[j] = max(whi[j], bh[j] + o)
    wide = engine.fit_scale(cam, wlo, whi, 0.97)

    g = 3.4 * R
    gy = ground - 4
    gcol = bytes((58, 64, 74))
    quad = [((cx - g, gy, cz - g), (cx + g, gy, cz - g), (cx + g, gy, cz + g)),
            ((cx - g, gy, cz - g), (cx + g, gy, cz + g), (cx - g, gy, cz + g))]
    groundp = [(t, gcol) for t in quad]
    return pieces, groundp, cam, tight, wide, cent, scatter, R, pre, hi[1] - lo[1], bbox


def schedule(cent, R, n):
    """(yaw, index_piece, t) par image. t=-1 : la piece est encore au tas."""
    fr, yaw, step = [], engine.YAW, 2 * math.pi / SLOTS
    for i in range(n):
        a, c = cent[i]
        if math.hypot(a, c) > MINR * R and (a * math.sin(yaw) + c * math.cos(yaw)) > BACK * R:
            tgt = round(math.atan2(-a, -c) / step) * step
            d = (tgt - yaw + math.pi) % (2 * math.pi) - math.pi
            if abs(d) > 1e-3:
                for f in range(1, ROT + 1):
                    t = f / ROT
                    fr.append((yaw + d * t * t * (3 - 2 * t), i, -1.0))
                yaw += d
        for k in range(FLY + SETTLE):
            fr.append((yaw, i, min(1.0, (k + 1) / FLY)))
    for s in range(SPIN):
        fr.append((yaw + 2 * math.pi * s / SPIN, n, 1.0))
    return fr


def _extents(box, yaw):
    """Les 8 coins d'une boite monde, en espace ecran, pour CE yaw."""
    cp, sp = math.cos(PITCH), math.sin(PITCH)
    cyw, syw = math.cos(yaw), math.sin(yaw)
    xlo = ylo = 1e18
    xhi = yhi = -1e18
    for bx in (box[0], box[3]):
        for by in (box[1], box[4]):
            for bz in (box[2], box[5]):
                sx = bx * cyw - bz * syw
                sy = by * cp + (bx * syw + bz * cyw) * sp
                if sx < xlo: xlo = sx
                if sx > xhi: xhi = sx
                if sy < ylo: ylo = sy
                if sy > yhi: yhi = sy
    yhi += HEAD * (yhi - ylo)                      # ciel : on voit arriver la piece
    return xlo, xhi, ylo, yhi


def _fit_box(box, yaw, fex, fey):
    """Cadre une boite englobante monde pour CE yaw : (cam, scale).

    Meme resolution en espace ecran que layers_zoom.frame_fit : les extremes sont pris
    apres projection, puis le centre camera est resolu pour les ramener au centre
    de l'image. `fex`/`fey` sont le plancher de champ -- sans eux, les premieres
    pieces, minuscules, feraient zoomer a l'absurde.
    """
    cp = math.cos(PITCH)
    cyw, syw = math.cos(yaw), math.sin(yaw)
    xlo, xhi, ylo, yhi = _extents(box, yaw)
    mx, my = (xlo + xhi) * 0.5, (ylo + yhi) * 0.5
    ex = max((xhi - xlo) * 0.5, fex, 1e-6)
    ey = max((yhi - ylo) * 0.5, fey, 1e-6)
    return (mx * cyw, my / cp, -mx * syw), min(W * MARGIN / (2 * ex), H * MARGIN / (2 * ey))


def _zoom_track(fr, pre, n):
    """(cam, echelle) par image, pour --zoom fit.

    L'echelle est un minimum courant : **la camera ne sait que reculer**. Sans ca
    elle pompe -- au debut sur une piece minuscule, et pendant le tour final ou
    un modele plat occupe plus ou moins l'ecran selon l'angle.

    C'est precalcule sur tout le film, pas accumule en cours de rendu : la piste
    ne depend que du planning, donc l'image reste fonction de son seul numero et
    tous les workers calculent la meme chose.
    """
    track, best = [], None
    for (yaw, cur, t) in fr:
        e = max(t, 0.0)
        e = e * e * (3 - 2 * e)
        xlo, xhi, ylo, yhi = _extents(pre[n], yaw)
        fex, fey = ZOOM_MIN * (xhi - xlo) * 0.5, ZOOM_MIN * (yhi - ylo) * 0.5
        ca, sa = _fit_box(pre[max(cur, 1)], yaw, fex, fey)
        cb, sb = _fit_box(pre[min(cur + 1, n)], yaw, fex, fey)
        sc = sa + (sb - sa) * e
        best = sc if best is None or sc < best else best
        track.append((tuple(ca[j] + (cb[j] - ca[j]) * e for j in range(3)), best))
    return track


def setup(mpd):
    """Tout ce qui ne depend pas du numero d'image : geometrie, tas, planning."""
    if MODE == 'notice':
        return setup_notice(mpd)
    pieces, groundp, cam, tight, wide, cent, scatter, R, pre, height, bbox = geometry(mpd)
    n = len(pieces)
    fr = schedule(cent, R, n)
    track = _zoom_track(fr, pre, n) if ZOOM == 'fit' else None
    return (pieces + [groundp], n, cam, tight, wide, scatter, R, fr, track)


def frame(st, idx):
    if MODE == 'notice':
        return frame_notice(st, idx)
    """Le tampon couleur de l'image idx. Ne depend que de idx : c'est ce qui rend
    le rendu parallelisable, et comparable image par image au portage C++."""
    allp, n, cam, tight, wide, scatter, R, fr, track = st
    cp, sp = math.cos(PITCH), math.sin(PITCH)
    arc = ARC * R
    yaw, cur, t = fr[idx]
    if ZOOM == 'fit':
        cam, scale = track[idx]
    else:
        p = min(1.0, (cur + max(t, 0.0)) / n / ZOOM_END)
        scale = wide + (tight - wide) * (p * p * (3 - 2 * p))
    flats = project(allp, cam, scale, yaw)
    cyw, syw = math.cos(yaw), math.sin(yaw)
    cb, zb = bytearray(BG), ZCLEAR[:]
    draw(cb, zb, flats[n])                                   # le sol d'abord
    for j in range(n):
        if j < cur:
            ox = oy = oz = 0.0
        else:
            if not NPILES and j > cur:
                continue        # sans tas, rien au sol : la piece a venir n'existe pas
            ox, oy, oz = scatter[j]
            if j == cur and t >= 0.0:
                e = t * t * (3 - 2 * t)
                ox *= 1 - e; oy *= 1 - e; oz *= 1 - e
                oy += arc * math.sin(math.pi * t)
        if ox or oy or oz:
            rx = ox * cyw - oz * syw
            rz = ox * syw + oz * cyw
            draw(cb, zb, flats[j], rx * scale,
                 -(oy * cp + rz * sp) * scale, -oy * sp + rz * cp)
        else:
            draw(cb, zb, flats[j])
    progress(cb, min(1.0, cur / n))
    return cb


def nframes(st):
    """Nombre d'images du film, quel que soit le mode."""
    return len(st[8] if st[0] == 'notice' else st[7])


def npieces(st):
    return st[2] if st[0] == 'notice' else st[1]


def _smooth(t):
    return t * t * (3 - 2 * t)


def _smooth5(t):
    """Lissage d'ordre superieur : derivee ET courbure nulles aux deux bouts.

    Le smoothstep ordinaire demarre avec une courbure non nulle, ce qui se voit
    sur un mouvement de camera -- le recul part sec.
    """
    return t * t * t * (t * (t * 6 - 15) + 10)


def _stage_box(q, ox, oy, oz):
    """La boite d'un sous-ensemble a l'atelier, ciel de chute compris."""
    d = DROP * max(q[3] - q[0], q[4] - q[1], q[5] - q[2])
    return [q[0] + ox, q[1] + oy, q[2] + oz, q[3] + ox, q[4] + oy + d, q[5] + oz]


def _lerp_fit(ca, sa, cb, sb, e):
    """Entre deux cadrages. L'echelle est GEOMETRIQUE : un zoom se percoit
    multiplicativement, une interpolation lineaire donne une vitesse inegale."""
    return tuple(ca[j] + (cb[j] - ca[j]) * e for j in range(3)), sa * math.pow(sb / sa, e)


def _union(bl, bh, q):
    """Etend la boite q (6 nombres) par [bl, bh]."""
    return [min(q[j], bl[j]) for j in range(3)] + [max(q[3 + j], bh[j]) for j in range(3)]


def setup_notice(mpd):
    """Montage en notice : chaque sous-ensemble se monte a l'ecart, puis vole.

    Les sous-ensembles viennent du .mpd lui-meme (les references de premier niveau
    du fichier principal) et sont des tranches CONTIGUES de la liste de pieces,
    cf. ldrawlib.group_sizes. Un groupe d'une seule piece n'a rien a assembler :
    la piece tombe directement a sa place.
    """
    pieces, groundp, cam, tight, wide, cent, scatter, R, pre, height, bbox = geometry(mpd)
    n = len(pieces)
    files, main = load_mpd(mpd)
    bounds, k = [], 0
    for sz in group_sizes(files, main):
        bounds.append((k, k + sz))
        k += sz
    ground = pre[n][1]

    # l'atelier : un point fixe a cote du modele, au sol
    sx, sz_ = cam[0] + STAGE_R * R, cam[2]
    groups, gpre = [], [None] * n
    for (a, b) in bounds:
        if b - a <= 1:
            groups.append(None)
        else:
            q = [1e18] * 3 + [-1e18] * 3
            for j in range(a, b):
                q = _union(bbox[j][0], bbox[j][1], q)
            groups.append((q, (sx - (q[0] + q[3]) * 0.5,
                               ground - q[1],
                               sz_ - (q[2] + q[5]) * 0.5)))
        # boite cumulee DANS le groupe : donne son propre cadrage, et la hauteur
        # de chute de chaque piece
        q = [1e18] * 3 + [-1e18] * 3
        for j in range(a, b):
            q = _union(bbox[j][0], bbox[j][1], q)
            gpre[j] = q

    fr = []
    yaw = engine.YAW
    step = 2 * math.pi / SLOTS

    def turn_xz(px, pz, subj):
        """Rotation a operer pour degager le point (px,pz) sur le sujet du plan.

        Meme regle que schedule() en mode tas, mais relative au SUJET : le
        sous-ensemble quand on est a l'atelier, le modele quand on lui rattache
        un bloc. Ce qui atterrit du cote cache ne se voit pas arriver ; le seuil
        de rayon ecarte ce qui est proche de l'axe, dont la direction n'est que
        du bruit.
        """
        a_ = px - (subj[0] + subj[3]) * 0.5
        c_ = pz - (subj[2] + subj[5]) * 0.5
        rad = 0.5 * math.hypot(subj[3] - subj[0], subj[5] - subj[2])
        if rad <= 0.0:
            return None
        if math.hypot(a_, c_) > MINR * rad and (a_ * math.sin(yaw) + c_ * math.cos(yaw)) > BACK * rad:
            tgt = round(math.atan2(-a_, -c_) / step) * step
            d = (tgt - yaw + math.pi) % (2 * math.pi) - math.pi
            if abs(d) > 1e-3:
                return d
        return None

    def turn_for(j, subj):
        bl, bh = bbox[j]
        return turn_xz((bl[0] + bh[0]) * 0.5, (bl[2] + bh[2]) * 0.5, subj)

    def swing(gi, j, d):
        for f in range(1, ROT + 1):
            fr.append((0, gi, j, -1.0, yaw + d * _smooth(f / ROT)))

    for gi, (a, b) in enumerate(bounds):
        if groups[gi] is None:
            d = turn_for(a, pre[b]) or 0.0
            if d:
                swing(gi, a, d)
                yaw += d
            for f in range(FLY + SETTLE):
                fr.append((0, gi, a, min(1.0, (f + 1) / FLY), yaw))
        else:
            for j in range(a, b):
                d = turn_for(j, gpre[j]) or 0.0
                if d:
                    swing(gi, j, d)
                    yaw += d
                for f in range(FLY + SETTLE):
                    fr.append((0, gi, j, min(1.0, (f + 1) / FLY), yaw))
            # le bloc vole ET la camera pivote, en un seul mouvement : elle
            # recule vers le modele en se placant face au point d'accroche
            q = groups[gi][0]
            d = turn_xz((q[0] + q[3]) * 0.5, (q[2] + q[5]) * 0.5, pre[b]) or 0.0
            for f in range(TRANSFER):
                t = (f + 1) / TRANSFER
                fr.append((1, gi, b - 1, t, yaw + d * _smooth5(t)))
            yaw += d
            for f in range(HOLD):
                fr.append((3, gi, b - 1, (f + 1) / HOLD, yaw))
    for f in range(SPIN):
        fr.append((2, len(bounds), n, f / SPIN, yaw + 2 * math.pi * f / SPIN))

    # piste camera : chaque image a son cadrage, calcule d'avance
    track = []
    for idx, (kind, gi, cur, t, yaw) in enumerate(fr):
        if kind == 2:
            camf, sc = _fit_box(pre[n], yaw, 0.0, 0.0)
        elif kind == 3:
            camf, sc = _fit_box(pre[bounds[gi][1]], yaw, 0.0, 0.0)
        elif kind == 0 and groups[gi] is None:
            ca, sa = _fit_box(pre[max(bounds[gi][0], 1)], yaw, 0.0, 0.0)
            cb_, sb = _fit_box(pre[bounds[gi][1]], yaw, 0.0, 0.0)
            camf, sc = _lerp_fit(ca, sa, cb_, sb, _smooth5(max(t, 0.0)))
        elif kind == 0:
            # le cadrage est interpole SUR LE VOL de la piece : sinon il saute
            # d'un cran a chaque piece posee, et le recul part sec
            a0 = bounds[gi][0]
            ox, oy, oz = groups[gi][1]
            qb = gpre[cur]
            qa = gpre[cur - 1] if cur > a0 else qb
            ca, sa = _fit_box(_stage_box(qa, ox, oy, oz), yaw, 0.0, 0.0)
            cb_, sb = _fit_box(_stage_box(qb, ox, oy, oz), yaw, 0.0, 0.0)
            camf, sc = _lerp_fit(ca, sa, cb_, sb, _smooth5(max(t, 0.0)))
        else:
            q, (ox, oy, oz) = groups[gi]
            # meme boite que la derniere image de montage, ciel de chute compris :
            # sans ca le cadrage saute d'un cran a l'entree du vol
            ca, sa = _fit_box(_stage_box(q, ox, oy, oz), yaw, 0.0, 0.0)
            cb_, sb = _fit_box(pre[bounds[gi][1]], yaw, 0.0, 0.0)
            camf, sc = _lerp_fit(ca, sa, cb_, sb, _smooth5(t))
        track.append((camf, sc, yaw))

    return ('notice', pieces + [groundp], n, R, pre, bounds, groups, gpre, fr, track)


def frame_notice(st, idx):
    """Le tampon couleur de l'image idx, en mode notice. Fonction du seul idx."""
    _, allp, n, R, pre, bounds, groups, gpre, fr, track = st
    kind, gi, cur, t = fr[idx][:4]
    camf, scale, yaw = track[idx]
    cp, sp = math.cos(PITCH), math.sin(PITCH)
    cyw, syw = math.cos(yaw), math.sin(yaw)
    flats = project(allp, camf, scale, yaw)
    cb, zb = bytearray(BG), ZCLEAR[:]
    draw(cb, zb, flats[n])                                   # le sol d'abord

    a, b = bounds[gi] if gi < len(bounds) else (n, n)
    for j in range(n):
        if kind == 3:
            # pause : le modele tel qu'il est CONSTRUIT, pas le modele fini
            if j >= b:
                continue
            ox = oy = oz = 0.0
        elif j < a:
            # a l'atelier on ne montre QUE le sous-ensemble : le modele deja
            # assemble n'apparait qu'au moment ou le bloc part s'y poser
            if kind == 0 and groups[gi] is not None:
                continue
            ox = oy = oz = 0.0                               # deja assemble
        elif kind == 2:
            ox = oy = oz = 0.0
        elif j >= b:
            continue                                         # pas encore construit
        elif groups[gi] is None:                             # piece seule
            q = gpre[j] or [0] * 6
            e = _smooth(max(t, 0.0))
            ox, oz = 0.0, 0.0
            oy = DROP * max(q[3] - q[0], q[4] - q[1], q[5] - q[2]) * (1 - e)
        elif kind == 0:
            if j > cur:
                continue
            ox, oy, oz = groups[gi][1]
            if j == cur:
                q = gpre[j]
                e = _smooth(max(t, 0.0))
                oy += DROP * max(q[3] - q[0], q[4] - q[1], q[5] - q[2]) * (1 - e)
        else:                                                # le bloc vole
            e = _smooth(t)
            ox, oy, oz = (c * (1 - e) for c in groups[gi][1])
        if ox or oy or oz:
            rx = ox * cyw - oz * syw
            rz = ox * syw + oz * cyw
            draw(cb, zb, flats[j], rx * scale,
                 -(oy * cp + rz * sp) * scale, -oy * sp + rz * cp)
        else:
            draw(cb, zb, flats[j])
    progress(cb, min(1.0, (n if kind == 2 else b if kind == 3 else a) / n))
    return cb


def render_range(mpd, a, b, outfile):
    st = setup(mpd)
    b = min(b, nframes(st))

    ff = subprocess.Popen(
        ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'image2pipe', '-vcodec', 'ppm',
         '-framerate', '30', '-i', '-', '-vf', 'scale=960:-2:flags=lanczos,format=yuv420p',
         '-c:v', 'libx264', '-crf', '20', '-preset', 'medium', outfile],
        stdin=subprocess.PIPE)
    out = ff.stdin
    t0 = time.time()

    for idx in range(a, b):
        out.write(HDR); out.write(bytes(frame(st, idx)))
        if (idx - a) % 25 == 0:
            print('[%s] %d/%d  %.0fs' % (os.path.basename(outfile), idx - a, b - a,
                                         time.time() - t0), file=sys.stderr, flush=True)
    out.close()
    ff.wait()


def main():
    configure(sys.argv)
    mpd = sys.argv[1]
    if '--range' in sys.argv:
        k = sys.argv.index('--range')
        a, b = int(sys.argv[k + 1]), int(sys.argv[k + 2])
        render_range(mpd, a, b, sys.argv[sys.argv.index('--out') + 1])
        return

    nproc = int(sys.argv[sys.argv.index('--parallel') + 1]) if '--parallel' in sys.argv else 8
    final = sys.argv[sys.argv.index('--out') + 1] if '--out' in sys.argv else 'film.mp4'
    st = setup(mpd)
    T = nframes(st)
    print('%d pieces, %d images (%.0f s de video), %d processus'
          % (npieces(st), T, T / 30, nproc), file=sys.stderr)

    extra = []
    for name in PILE_OPTS:                      # les workers doivent voir les memes tas
        if name in sys.argv:
            extra += [name, sys.argv[sys.argv.index(name) + 1]]

    seg, procs = [], []
    cut = [T * k // nproc for k in range(nproc + 1)]
    for k in range(nproc):
        s = 'seg_%02d.mp4' % k
        seg.append(s)
        procs.append(subprocess.Popen([sys.executable, os.path.abspath(__file__), mpd,
                                       '--range', str(cut[k]), str(cut[k + 1]), '--out', s] + extra,
                                      stderr=open('seg_%02d.log' % k, 'w')))
    t0 = time.time()
    for pr in procs:
        pr.wait()
    print('segments rendus en %.0fs' % (time.time() - t0), file=sys.stderr)

    with open('concat.txt', 'w') as f:
        for s in seg:
            f.write("file '%s'\n" % s)
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0',
                    '-i', 'concat.txt', '-c', 'copy', final], check=True)
    print('=> %s  (%.0fs total)' % (final, time.time() - t0), file=sys.stderr)


if __name__ == '__main__':
    main()
