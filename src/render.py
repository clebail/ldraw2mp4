#!/usr/bin/env python3
"""Rendu d'une animation de construction LEGO -> flux PPM sur stdout.
Projection orthographique, flat shading, painter's algorithm, spans en bytearray.
Unites LDraw : 1 tenon = 20 LDU, 1 brique = 24 LDU, 1 plate = 8 LDU."""
import sys, math

W, H = 1440, 960
FPS = 30
DROP, SETTLE, SPIN = 16, 6, 90

S, B, P = 20, 24, 8  # tenon, brique, plate

YEL, ORA, BLK = (242, 205, 55), (254, 138, 24), (27, 42, 52)
PLATE_COL = (160, 165, 170)

# (couleur, x0,x1, y0,y1, z0,z1) -- ordre = ordre de montage
PIECES = [
    (YEL,   0,  80,  0, 24,   0, 40),   # 1 corps  (2x4)
    (YEL,  40,  80, 24, 48,   0, 40),   # 2 cou    (2x2)
    (YEL,   0,  20, 24, 48,   0, 40),   # 3 queue  (1x2)
    (YEL,  40,  80, 48, 72,   0, 40),   # 4 tete   (2x2)
    (ORA,  80, 100, 48, 72,   0, 40),   # 5 bec    (1x2)
    (BLK,  40,  60, 72, 80,   0, 20),   # 6 oeil   (plate 1x1)
]
BASE = (PLATE_COL, -40, 120, -8, 0, -40, 80)

CX, CZ = 40.0, 20.0
PITCH = math.radians(28)
SCALE = 4.55
OX, OY = W * 0.5, H * 0.60

# eclairage fixe en repere monde, par axe de face
SHADE = {'+y': 1.00, '-y': 0.30, '+x': 0.50, '-x': 0.64, '+z': 0.42, '-z': 0.86}


def shaded(col, k):
    return bytes((min(255, int(c * k)) for c in col))


TILE = 20.0
NRM = {'+y': (0, 1, 0), '-y': (0, -1, 0), '+x': (1, 0, 0),
       '-x': (-1, 0, 0), '+z': (0, 0, 1), '-z': (0, 0, -1)}


def _grid(a, b):
    """Decoupe [a,b] en tranches de TILE max."""
    n = max(1, int(math.ceil((b - a) / TILE)))
    step = (b - a) / n
    return [(a + i * step, a + (i + 1) * step) for i in range(n)]


def box_faces(col, x0, x1, y0, y1, z0, z1):
    """6 faces subdivisees en tuiles <= 1 tenon (fiabilise le tri en profondeur)."""
    f = []
    for xa, xb in _grid(x0, x1):
        for za, zb in _grid(z0, z1):
            f.append(([(xa, y1, za), (xb, y1, za), (xb, y1, zb), (xa, y1, zb)], '+y', col))
            f.append(([(xa, y0, za), (xb, y0, za), (xb, y0, zb), (xa, y0, zb)], '-y', col))
    for ya, yb in _grid(y0, y1):
        for za, zb in _grid(z0, z1):
            f.append(([(x1, ya, za), (x1, yb, za), (x1, yb, zb), (x1, ya, zb)], '+x', col))
            f.append(([(x0, ya, za), (x0, yb, za), (x0, yb, zb), (x0, ya, zb)], '-x', col))
        for xa, xb in _grid(x0, x1):
            f.append(([(xa, ya, z1), (xb, ya, z1), (xb, yb, z1), (xa, yb, z1)], '+z', col))
            f.append(([(xa, ya, z0), (xb, ya, z0), (xb, yb, z0), (xa, yb, z0)], '-z', col))
    return f


def stud_faces(col, cx, cz, ytop, r=6.2, h=4.0, n=10):
    """Prisme n-gonal = un tenon."""
    ring = [(cx + r * math.cos(2 * math.pi * i / n), cz + r * math.sin(2 * math.pi * i / n))
            for i in range(n)]
    out = [([(x, ytop + h, z) for x, z in ring], '+y', col)]
    for i in range(n):
        ax, az = ring[i]
        bx, bz = ring[(i + 1) % n]
        nx, nz = (ax + bx) / 2 - cx, (az + bz) / 2 - cz
        key = ('+x' if nx > 0 else '-x') if abs(nx) > abs(nz) else ('+z' if nz > 0 else '-z')
        out.append(([(ax, ytop, az), (bx, ytop, bz), (bx, ytop + h, bz), (ax, ytop + h, az)], key, col))
    return out


def studs_for(box, occluders):
    col, x0, x1, y0, y1, z0, z1 = box
    faces = []
    sx = x0 + S / 2
    while sx < x1:
        sz = z0 + S / 2
        while sz < z1:
            hidden = any(c[1] < sx < c[2] and c[5] < sz < c[6] and c[3] <= y1 + 2 <= c[4]
                         for c in occluders)
            if not hidden:
                faces += stud_faces(col, sx, sz, y1)
            sz += S
        sx += S
    return faces


def render(boxes, occluders, yaw, done):
    cy, sy_ = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(PITCH), math.sin(PITCH)

    faces = []
    for bx in boxes:
        faces += box_faces(*bx)
        faces += studs_for(bx, occluders)

    # backface culling : normale dont la composante de profondeur s'eloigne
    vis = {}
    for k, (nx, ny, nz) in NRM.items():
        vis[k] = (-ny * sp + (nx * sy_ + nz * cy) * cp) < 0

    prepared = []
    for pts, key, col in faces:
        if not vis[key]:
            continue
        proj, dsum = [], 0.0
        for (x, y, z) in pts:
            ax, az = x - CX, z - CZ
            rx = ax * cy - az * sy_
            rz = ax * sy_ + az * cy
            d = -y * sp + rz * cp
            proj.append((OX + rx * SCALE, OY - (y * cp + rz * sp) * SCALE))
            dsum += d
        prepared.append((dsum / len(proj), proj, shaded(col, SHADE[key])))

    prepared.sort(key=lambda t: -t[0])

    buf = bytearray(BG)
    for _, poly, colb in prepared:
        fill(buf, poly, colb)

    # jauge d'etapes : 6 pastilles
    for i in range(len(PIECES)):
        c = b'\xff\xd7\x3c' if i < done else b'\x3a\x40\x48'
        bar(buf, int(W / 2 - 6 * 26 + i * 52), H - 58, 34, 8, c)
    return buf


def fill(buf, poly, colb):
    n = len(poly)
    ys = [p[1] for p in poly]
    y0 = max(0, int(math.floor(min(ys))))
    y1 = min(H - 1, int(math.ceil(max(ys))))
    for y in range(y0, y1 + 1):
        yc = y + 0.5
        xmin, xmax = 1e9, -1e9
        for i in range(n):
            xa, ya = poly[i]
            xb, yb = poly[(i + 1) % n]
            if (ya <= yc < yb) or (yb <= yc < ya):
                t = (yc - ya) / (yb - ya)
                x = xa + t * (xb - xa)
                if x < xmin: xmin = x
                if x > xmax: xmax = x
        if xmax < xmin:
            continue
        a = max(0, int(xmin + 0.5))
        b = min(W, int(xmax + 0.5))
        if b <= a:
            continue
        o = (y * W + a) * 3
        buf[o:o + (b - a) * 3] = colb * (b - a)


def bar(buf, x, y, w, h, colb):
    for yy in range(y, y + h):
        if 0 <= yy < H:
            o = (yy * W + x) * 3
            buf[o:o + w * 3] = colb * w


# fond degrade, calcule une fois
BG = bytearray(W * H * 3)
for y in range(H):
    t = y / H
    c = bytes((int(38 + 26 * t), int(44 + 30 * t), int(54 + 34 * t)))
    o = y * W * 3
    BG[o:o + W * 3] = c * W

hdr = b'P6\n%d %d\n255\n' % (W, H)
out = sys.stdout.buffer


def emit(boxes, occ, yaw, done):
    out.write(hdr)
    out.write(render(boxes, occ, yaw, done))


settled = []
total_build = len(PIECES) * (DROP + SETTLE)
frame = 0

for i, pc in enumerate(PIECES):
    col, x0, x1, y0, y1, z0, z1 = pc
    for f in range(DROP + SETTLE):
        yaw = math.radians(22 + 26 * (frame / total_build))
        if f < DROP:
            t = f / (DROP - 1)
            e = 1 - (1 - t) ** 3           # ease-out cubique
            off = 150 * (1 - e)
            moving = (col, x0, x1, y0 + off, y1 + off, z0, z1)
            boxes = [BASE] + settled + [moving]
            emit(boxes, [BASE] + settled, yaw, i)
        else:
            boxes = [BASE] + settled + [pc]
            emit(boxes, [BASE] + settled + [pc], yaw, i + 1)
        frame += 1
    settled.append(pc)

for f in range(SPIN):
    yaw = math.radians(48) + 2 * math.pi * f / SPIN
    emit([BASE] + settled, [BASE] + settled, yaw, len(PIECES))
