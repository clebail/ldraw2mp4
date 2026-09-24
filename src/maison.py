#!/usr/bin/env python3
"""Petite maison LEGO ~100 pieces : genere maison.ldr + flux PPM sur stdout.
Modele pose sur une grille (1 tenon x 1 brique), ce qui permet de supprimer
toutes les faces internes : seule la coque exterieure est rendue."""
import sys, math

W, H = 1440, 960
DROP, SETTLE, SPIN = 6, 1, 120
S, B = 20, 24                      # tenon (X/Z), hauteur de brique (Y)

TAN   = ((222, 198, 156), 19)
RED   = ((181, 52, 40),    4)
BROWN = ((88, 57, 39),     6)
GLASS = ((163, 214, 232), 43)
GREY  = ((110, 113, 111), 72)
PLATE = ((160, 165, 170),  7)

GX0, GX1, GZ0, GZ1 = 0, 12, 0, 8   # emprise de la maison, en tenons
COURSES = 6                        # hauteur des murs

# ---------------------------------------------------------------- le modele

def build():
    """Retourne la liste des pieces dans l'ordre de montage.
    Piece = (couleur, ix0, ix1, iy0, iy1, iz0, iz1)."""
    pieces = []

    # ouvertures : cellules de mur a ne pas maconner (porte + fenetres)
    door = {(x, y, 0) for x in (5, 6) for y in (0, 1, 2)}
    wins = set()
    for (x0, z0, horiz) in [(2, 0, 1), (8, 0, 1), (2, GZ1 - 1, 1), (8, GZ1 - 1, 1)]:
        for y in (2, 3):
            wins |= {(x0, y, z0), (x0 + 1, y, z0)}
    for (x0, z0) in [(GX0, 3), (GX1 - 1, 3)]:
        for y in (2, 3):
            wins |= {(x0, y, z0), (x0, y, z0 + 1)}
    holes = door | wins

    def split_run(n, stagger):
        segs = []
        if stagger and n >= 2:
            segs.append(2); n -= 2
        while n > 0:
            L = 4 if n >= 6 or n == 4 else (3 if n in (3, 5) else (2 if n == 2 else 1))
            segs.append(L); n -= L
        return segs

    # murs, assise par assise, en tournant autour de la maison
    for y in range(COURSES):
        runs = [
            [(x, 0) for x in range(GX0, GX1)],                    # facade
            [(GX1 - 1, z) for z in range(1, GZ1 - 1)],            # pignon droit
            [(x, GZ1 - 1) for x in range(GX1 - 1, GX0 - 1, -1)],  # arriere
            [(GX0, z) for z in range(GZ1 - 2, 0, -1)],            # pignon gauche
        ]
        for run in runs:
            sub, cur = [], []
            for (x, z) in run:                     # coupe le run sur les ouvertures
                if (x, y, z) in holes:
                    if cur: sub.append(cur); cur = []
                else:
                    cur.append((x, z))
            if cur: sub.append(cur)
            for seg in sub:
                i = 0
                for L in split_run(len(seg), y % 2 == 1):
                    cells = seg[i:i + L]; i += L
                    xs = [c[0] for c in cells]; zs = [c[1] for c in cells]
                    pieces.append((TAN, min(xs), max(xs) + 1, y, y + 1, min(zs), max(zs) + 1))
        # menuiseries de l'assise courante
        for (x, yy, z) in sorted(door | wins):
            if yy != y:
                continue
            col = BROWN if (x, yy, z) in door else GLASS
            if (x + 1, yy, z) in holes and x % 2 == 0 or ((x, yy, z) in door and x == 5):
                pieces.append((col, x, x + 2, y, y + 1, z, z + 1))
            elif (x, yy, z + 1) in holes and z % 2 == 1:
                pieces.append((col, x, x + 1, y, y + 1, z, z + 2))

    # toit en gradins, de bas en haut, en 2x4
    y = COURSES
    for inset in range(4):
        z0, z1 = GZ0 + inset, GZ1 - inset
        if z1 - z0 < 2:
            break
        for z in range(z0, z1, 2):
            for x in range(GX0, GX1, 4):
                pieces.append((RED, x, x + 4, y, y + 1, z, min(z + 2, z1)))
        y += 1

    # cheminee
    for k in range(3):
        pieces.append((GREY, 9, 11, y + k - 1, y + k, 3, 5))
    return pieces


# ------------------------------------------------------------- geometrie 3D

NRM = {'+y': (0, 1, 0), '-y': (0, -1, 0), '+x': (1, 0, 0),
       '-x': (-1, 0, 0), '+z': (0, 0, 1), '-z': (0, 0, -1)}
DELTA = {'+y': (0, 1, 0), '-y': (0, -1, 0), '+x': (1, 0, 0),
         '-x': (-1, 0, 0), '+z': (0, 0, 1), '-z': (0, 0, -1)}
SHADE = {'+y': 1.00, '-y': 0.30, '+x': 0.50, '-x': 0.64, '+z': 0.42, '-z': 0.86}


def quad(key, x, y, z, dy=B):
    """Face d'une cellule unite, coin (x,y,z) en LDU."""
    x1, y1, z1 = x + S, y + dy, z + S
    if key == '+y': return [(x, y1, z), (x1, y1, z), (x1, y1, z1), (x, y1, z1)]
    if key == '-y': return [(x, y, z), (x1, y, z), (x1, y, z1), (x, y, z1)]
    if key == '+x': return [(x1, y, z), (x1, y1, z), (x1, y1, z1), (x1, y, z1)]
    if key == '-x': return [(x, y, z), (x, y1, z), (x, y1, z1), (x, y, z1)]
    if key == '+z': return [(x, y, z1), (x1, y, z1), (x1, y1, z1), (x, y1, z1)]
    return [(x, y, z), (x1, y, z), (x1, y1, z), (x, y1, z)]


def stud(col, cx, cz, ytop, r=6.2, h=4.0, n=8):
    ring = [(cx + r * math.cos(2 * math.pi * i / n), cz + r * math.sin(2 * math.pi * i / n))
            for i in range(n)]
    out = [([(x, ytop + h, z) for x, z in ring], '+y', col)]
    for i in range(n):
        ax, az = ring[i]; bx, bz = ring[(i + 1) % n]
        nx, nz = (ax + bx) / 2 - cx, (az + bz) / 2 - cz
        k = ('+x' if nx > 0 else '-x') if abs(nx) > abs(nz) else ('+z' if nz > 0 else '-z')
        out.append(([(ax, ytop, az), (bx, ytop, bz), (bx, ytop + h, bz), (ax, ytop + h, az)], k, col))
    return out


def faces_of(piece, occ, yoff=0.0):
    """Coque d'une piece : on ne garde que les faces dont la cellule voisine est vide."""
    (col, _), x0, x1, y0, y1, z0, z1 = piece
    out = []
    for ix in range(x0, x1):
        for iy in range(y0, y1):
            for iz in range(z0, z1):
                px, py, pz = ix * S, iy * B + yoff, iz * S
                for key, (dx, dy, dz) in DELTA.items():
                    if (ix + dx, iy + dy, iz + dz) in occ:
                        continue
                    out.append((quad(key, px, py, pz), key, col))
                if (ix, iy + 1, iz) not in occ:
                    out += stud(col, px + S / 2, pz + S / 2, py + B)
    return out


def plate_faces(occ):
    """Socle : plaque de base + tenons libres."""
    col = PLATE[0]
    x0, x1, z0, z1 = (GX0 - 2) * S, (GX1 + 2) * S, (GZ0 - 2) * S, (GZ1 + 2) * S
    y0, y1 = -8, 0
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1),
         (x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)]
    out = [([v[0], v[1], v[2], v[3]], '-y', col), ([v[1], v[5], v[6], v[2]], '+x', col),
           ([v[0], v[4], v[7], v[3]], '-x', col), ([v[3], v[2], v[6], v[7]], '+z', col),
           ([v[0], v[1], v[5], v[4]], '-z', col)]
    for ix in range(GX0 - 2, GX1 + 2):
        for iz in range(GZ0 - 2, GZ1 + 2):
            out.append((quad('+y', ix * S, -8, iz * S, dy=8), '+y', col))
            if (ix, 0, iz) not in occ:
                out += stud(col, ix * S + S / 2, iz * S + S / 2, 0)
    return out


# ------------------------------------------------------------------- rendu

CX, CZ = (GX0 + GX1) / 2 * S, (GZ0 + GZ1) / 2 * S
PITCH = math.radians(27)
SCALE, OX, OY = 2.12, W * 0.5, H * 0.76


def shaded(col, k):
    return bytes(min(255, int(c * k)) for c in col)


BG = bytearray(W * H * 3)
for y in range(H):
    t = y / H
    BG[y * W * 3:(y + 1) * W * 3] = bytes((int(38 + 26 * t), int(44 + 30 * t), int(54 + 34 * t))) * W


def fill(buf, poly, colb):
    n = len(poly)
    ys = [p[1] for p in poly]
    for y in range(max(0, int(min(ys))), min(H - 1, int(max(ys)) + 1) + 1):
        yc = y + 0.5
        lo, hi = 1e9, -1e9
        for i in range(n):
            xa, ya = poly[i]; xb, yb = poly[(i + 1) % n]
            if (ya <= yc < yb) or (yb <= yc < ya):
                x = xa + (yc - ya) / (yb - ya) * (xb - xa)
                if x < lo: lo = x
                if x > hi: hi = x
        if hi < lo:
            continue
        a = max(0, int(lo + 0.5)); b = min(W, int(hi + 0.5))
        if b > a:
            o = (y * W + a) * 3
            buf[o:o + (b - a) * 3] = colb * (b - a)


def bar(buf, frac):
    x0, y0, w, h = int(W * 0.25), H - 54, int(W * 0.5), 10
    for yy in range(y0, y0 + h):
        o = (yy * W + x0) * 3
        buf[o:o + w * 3] = b'\x3a\x40\x48' * w
        n = int(w * frac)
        if n:
            buf[o:o + n * 3] = b'\xff\xd7\x3c' * n


hdr = b'P6\n%d %d\n255\n' % (W, H)
out = sys.stdout.buffer


def emit(static, extra, yaw, frac):
    cy, sy_ = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(PITCH), math.sin(PITCH)
    vis = {k: (-ny * sp + (nx * sy_ + nz * cy) * cp) < 0 for k, (nx, ny, nz) in NRM.items()}

    prep = []
    for pts, key, col in static + extra:
        if not vis[key]:
            continue
        proj, d = [], 0.0
        for (x, y, z) in pts:
            ax, az = x - CX, z - CZ
            rx = ax * cy - az * sy_
            rz = ax * sy_ + az * cy
            d += -y * sp + rz * cp
            proj.append((OX + rx * SCALE, OY - (y * cp + rz * sp) * SCALE))
        prep.append((d / len(pts), proj, shaded(col, SHADE[key])))
    prep.sort(key=lambda t: -t[0])

    buf = bytearray(BG)
    for _, poly, colb in prep:
        fill(buf, poly, colb)
    bar(buf, frac)
    out.write(hdr); out.write(buf)


# ------------------------------------------------------------- export LDraw

PARTS = {(1, 1): '3005', (2, 1): '3004', (3, 1): '3622', (4, 1): '3010',
         (6, 1): '3009', (2, 2): '3003', (4, 2): '3001'}


def write_ldr(pieces, path):
    L = ['0 Petite maison', '0 Name: maison.ldr', '0 Author: Claude',
         '0 !LDRAW_ORG Unofficial_Model', '']
    for n, ((_, code), x0, x1, y0, y1, z0, z1) in enumerate(pieces, 1):
        dx, dz = x1 - x0, z1 - z0
        rot = dz > dx
        key = (max(dx, dz), min(dx, dz))
        part = PARTS.get(key)
        if part is None:
            continue
        m = '0 0 1 0 1 0 -1 0 0' if rot else '1 0 0 0 1 0 0 0 1'
        L.append('1 %d %g %g %g %s %s.dat' % (
            code, (x0 + x1) / 2 * S, -y0 * B, (z0 + z1) / 2 * S, m, part))
        L.append('0 STEP')
    open(path, 'w').write('\n'.join(L) + '\n')


# --------------------------------------------------------------------- main

pieces = build()
write_ldr(pieces, sys.argv[1] if len(sys.argv) > 1 else 'maison.ldr')
if '--count' in sys.argv:
    print('pieces:', len(pieces), file=sys.stderr); sys.exit()

def settle(pieces_done, occ):
    """Recalcule la coque complete : appele seulement quand une piece se pose."""
    sh = []
    for p in pieces_done:
        sh += faces_of(p, occ)
    return sh + plate_faces(occ)


occ = set()
if '--still' in sys.argv:
    for (_, x0, x1, y0, y1, z0, z1) in ((p[0],) + p[1:] for p in pieces):
        pass
    for p in pieces:
        _, x0, x1, y0, y1, z0, z1 = p
        for ix in range(x0, x1):
            for iy in range(y0, y1):
                for iz in range(z0, z1):
                    occ.add((ix, iy, iz))
    emit(settle(pieces, occ), [], math.radians(50), 1.0)
    sys.exit()

static = settle([], occ)
total = len(pieces) * (DROP + SETTLE)
frame = 0

for i, pc in enumerate(pieces):
    _, x0, x1, y0, y1, z0, z1 = pc
    for f in range(DROP + SETTLE):
        yaw = math.radians(20 + 30 * (frame / total))
        if f < DROP:
            t = f / (DROP - 1)
            off = 200 * (1 - t) ** 3
            emit(static, faces_of(pc, set(), off), yaw, i / len(pieces))
        else:
            for ix in range(x0, x1):
                for iy in range(y0, y1):
                    for iz in range(z0, z1):
                        occ.add((ix, iy, iz))
            static = settle(pieces[:i + 1], occ)
            emit(static, [], yaw, (i + 1) / len(pieces))
        frame += 1

for f in range(SPIN):
    emit(static, [], math.radians(50) + 2 * math.pi * f / SPIN, 1.0)
