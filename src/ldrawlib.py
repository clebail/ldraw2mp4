#!/usr/bin/env python3
"""Parser LDraw minimal mais complet pour le rendu de surfaces.

Gere : .mpd multi-FILE, refs recursives (type 1) avec composition de matrices,
triangles et quads (types 3/4), couleur heritee 16, couleur de bord 24 ignoree,
resolution parts/ p/ parts/s/ p/48/. Les lignes (types 2/5) sont ignorees.
Pas de BFC : le rendu est two-sided, la normale est retournee vers la camera.
"""
import os


class Lib:
    def __init__(self, root):
        if not os.path.isdir(os.path.join(root, 'parts')):
            raise SystemExit("bibliotheque LDraw introuvable : %s/parts n'existe pas\n"
                             "  telechargement : cf. README section 1" % os.path.normpath(root))
        self.files = {}
        for sub in ('parts', 'p', 'models'):          # parts/ prioritaire sur p/
            base = os.path.join(root, sub)
            if not os.path.isdir(base):
                continue
            for dirpath, _, names in os.walk(base):
                rel = os.path.relpath(dirpath, base).replace(os.sep, '/').lower()
                for n in names:
                    if not n.lower().endswith(('.dat', '.ldr')):
                        continue
                    full = os.path.join(dirpath, n)
                    if rel != '.':
                        self.files.setdefault(rel + '/' + n.lower(), full)
                    self.files.setdefault(n.lower(), full)
        self.colours = self._colours(root)
        self.cache = {}
        self.sub = {}                                  # sous-modeles du .mpd

    @staticmethod
    def _colours(root):
        out = {}
        p = os.path.join(root, 'LDConfig.ldr')
        if not os.path.exists(p):
            return out
        for ln in open(p, errors='replace'):
            t = ln.split()
            if len(t) < 4 or t[1] != '!COLOUR':
                continue
            try:
                code = int(t[t.index('CODE') + 1])
                v = t[t.index('VALUE') + 1].lstrip('#')
                out[code] = (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
            except (ValueError, IndexError):
                continue
        return out

    def rgb(self, code):
        return self.colours.get(code, (140, 140, 140))

    def geom(self, name):
        """Triangles aplatis dans le repere local du fichier : [(p1,p2,p3,colour)]."""
        key = name.lower().replace('\\', '/')
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        self.cache[key] = []                           # garde-fou anti-recursion
        lines = self.sub.get(key)
        if lines is None:
            path = self.files.get(key) or self.files.get(os.path.basename(key))
            if path is None:
                return []
            lines = open(path, errors='replace').read().splitlines()

        tris = []
        for ln in lines:
            t = ln.split()
            if len(t) < 2:
                continue
            k = t[0]
            if k == '1' and len(t) >= 15:
                try:
                    c = int(t[1]); f = ' '.join(t[14:])
                    x, y, z, a, b, cc, d, e, ff, g, h, i = (float(v) for v in t[2:14])
                except ValueError:
                    continue
                for (p1, p2, p3, col) in self.geom(f):
                    nc = c if col == 16 else col
                    tris.append((
                        (a * p1[0] + b * p1[1] + cc * p1[2] + x,
                         d * p1[0] + e * p1[1] + ff * p1[2] + y,
                         g * p1[0] + h * p1[1] + i * p1[2] + z),
                        (a * p2[0] + b * p2[1] + cc * p2[2] + x,
                         d * p2[0] + e * p2[1] + ff * p2[2] + y,
                         g * p2[0] + h * p2[1] + i * p2[2] + z),
                        (a * p3[0] + b * p3[1] + cc * p3[2] + x,
                         d * p3[0] + e * p3[1] + ff * p3[2] + y,
                         g * p3[0] + h * p3[1] + i * p3[2] + z),
                        nc))
            elif k in ('3', '4'):
                n = 9 if k == '3' else 12
                if len(t) < 2 + n:
                    continue
                try:
                    c = int(t[1]); v = [float(x) for x in t[2:2 + n]]
                except ValueError:
                    continue
                if c == 24:
                    continue
                p = [(v[j], v[j + 1], v[j + 2]) for j in range(0, n, 3)]
                tris.append((p[0], p[1], p[2], c))
                if k == '4':
                    tris.append((p[0], p[2], p[3], c))
        self.cache[key] = tris
        return tris


def load_mpd(path):
    """Decoupe un .mpd en {nom_minuscule: lignes}, plus le nom du modele principal."""
    files, order, cur = {}, [], None
    for ln in open(path, errors='replace'):
        s = ln.rstrip('\n').strip()
        low = s.lower()
        if low.startswith('0 file '):
            cur = low[7:].strip()
            files[cur] = []
            order.append(cur)
        elif cur is None:
            cur = '__main__'
            files[cur] = [s]
            order.append(cur)
        else:
            files[cur].append(s)
    return files, order[0] if order else None


IDENT = (1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)


def compose(P, C):
    """Compose deux transformations (m00..m22, tx,ty,tz)."""
    a, b, c, d, e, f, g, h, i, tx, ty, tz = P
    A, B, C_, D, E, F, G, H, I, TX, TY, TZ = C
    return (a * A + b * D + c * G, a * B + b * E + c * H, a * C_ + b * F + c * I,
            d * A + e * D + f * G, d * B + e * E + f * H, d * C_ + e * F + f * I,
            g * A + h * D + i * G, g * B + h * E + i * H, g * C_ + h * F + i * I,
            a * TX + b * TY + c * TZ + tx,
            d * TX + e * TY + f * TZ + ty,
            g * TX + h * TY + i * TZ + tz)


def group_sizes(files, name):
    """Nombre de pieces feuilles par reference de premier niveau du modele principal.

    Les pieces d'un sous-modele sont CONTIGUES dans le resultat de placements(),
    qui descend en profondeur dans l'ordre du fichier : les tailles cumulees
    donnent donc les frontieres des sous-ensembles, sans rien changer a
    placements() lui-meme. Une reference vers une piece (et non un sous-modele)
    compte pour un groupe de 1.
    """
    while True:
        out, refs = [], []
        for ln in files.get(name, []):
            t = ln.split()
            if len(t) < 15 or t[0] != '1':
                continue
            try:
                int(t[1])
                [float(v) for v in t[2:14]]
            except ValueError:
                continue
            ref = ' '.join(t[14:]).lower().replace('\\', '/')
            refs.append(ref)
            if ref in files:
                sub = []
                placements(files, ref, IDENT, 16, sub, 1)
                out.append(len(sub))
            else:
                out.append(1)
        # un niveau qui ne reference qu'un seul sous-modele n'offre aucun
        # decoupage : on descend jusqu'au premier qui en propose plusieurs.
        # Le 10143 est dans ce cas -- son principal ne contient que Core.ldr.
        if len(out) == 1 and refs and refs[0] in files:
            name = refs[0]
            continue
        return out


def placements(files, name, xform=IDENT, colour=16, out=None, depth=0):
    """Aplatit le modele en une liste de poses de pieces, dans l'ordre de montage.
    Un sous-modele est monte entierement avant d'etre pose (comme une vraie notice)."""
    if out is None:
        out = []
    if depth > 32:
        return out
    for ln in files.get(name, []):
        t = ln.split()
        if len(t) < 15 or t[0] != '1':
            continue
        try:
            c = int(t[1])
            x, y, z, a, b, cc, d, e, ff, g, h, i = (float(v) for v in t[2:14])
        except ValueError:
            continue
        ref = ' '.join(t[14:]).lower().replace('\\', '/')
        M = compose(xform, (a, b, cc, d, e, ff, g, h, i, x, y, z))
        col = colour if c == 16 else c
        if ref in files:
            placements(files, ref, M, col, out, depth + 1)
        else:
            out.append((ref, col, M))
    return out
