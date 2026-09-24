#!/usr/bin/env python3
"""Genere la reference de parite du portage C++ : une image fixe, et en option
les triangles projetes qui l'ont produite.

    ref_still.py <model.ldr|mpd> [--yaw DEG] [--size WxH] [--margin F]
                 [--ppm out.ppm[.gz]] [--tris out.bin]

Passe par `src/engine.py` -- le module que le binaire C++ doit remplacer -- et
non par les pilotes de montage, qui en ont chacun une copie.

La paire (tris.bin, ref.ppm) sert a valider en DEUX temps, ce qui isole le seul
risque eleve du portage :

1. rastériseur seul  : le C++ lit `tris.bin` et doit ecrire exactement `ref.ppm`.
   Aucun parseur en jeu, donc un ecart ne peut venir que du rastériseur.
2. parseur           : le C++ produit son propre `tris.bin` et on le compare a
   celui-ci, octet par octet. Un parseur faux echoue alors a un offset precis
   au lieu de rendre une image plausible.

Format du dump : en-tete `<8sIIQ` (magie `LDRTRIS1`, largeur, hauteur, nombre
de triangles), puis un `<9d3B` par triangle (75 octets : ax ay az bx by bz
cx cy cz, puis rgb).

⚠️ Le dump est en float64, PAS en float32 : on vise le pixel exact, et arrondir
les sommets en float32 d'un cote seulement suffirait a faire diverger des pixels
de bord. Le C++ lit donc des doubles, comme Python.

⚠️ Le tampon de profondeur est en float32 alors que l'arithmetique est en
double : `array('f')` cote Python, donc `std::vector<float>` avec des `double`
en calcul cote C++. Un zbuf en double
donnerait des pixels differents sur les surfaces tangentes.
"""
import sys, os, math, time, gzip, struct, hashlib, contextlib
from array import array

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'src'))
import engine                                                   # noqa: E402

PACK = struct.Struct('<9d3B')
# en-tete du dump : sans les dimensions, le binaire devrait les deviner et on
# comparerait une image a une reference d'une autre taille sans s'en apercevoir
HEAD = struct.Struct('<8sIIQ')
MAGIC = b'LDRTRIS1'


def opt(av, name, default=None):
    return av[av.index(name) + 1] if name in av else default


@contextlib.contextmanager
def _open_w(path):
    raw = open(path, 'wb')
    try:
        if path.endswith('.gz'):
            # mtime=0 et nom vide : deux generations successives donnent le meme
            # fichier au bit pres. Sans ca l'en-tete gzip change a chaque `make`
            # et git voit une reference modifiee qui ne l'est pas.
            with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as gz:
                yield gz
        else:
            yield raw
    finally:
        raw.close()


def main():
    av = sys.argv[1:]
    if not av or '-h' in av or '--help' in av:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    path = av[0]

    if '--size' in av:
        w, h = (int(v) for v in opt(av, '--size').lower().split('x'))
        # BG et ZCLEAR sont construits a l'import, sur l'ancienne taille
        engine.W, engine.H = w, h
        engine.BG = bytearray(w * h * 3)
        for y in range(h):
            t = y / h
            engine.BG[y * w * 3:(y + 1) * w * 3] = bytes((int(36 + 26 * t), int(42 + 30 * t), int(52 + 34 * t))) * w
        engine.ZCLEAR = array('f', [engine.FAR]) * (w * h)
        engine.HDR = b'P6\n%d %d\n255\n' % (w, h)
    if '--yaw' in av:
        engine.YAW = math.radians(float(opt(av, '--yaw')))
    margin = float(opt(av, '--margin', 0.90))

    t0 = time.time()
    pieces, lo, hi = engine.load(path)
    cam, scale = engine.make_cam(lo, hi, margin)
    ntri = sum(len(p) for p in pieces)
    print('%d pieces, %d triangles, echelle %.6f  (%.1fs)'
          % (len(pieces), ntri, scale, time.time() - t0), file=sys.stderr)

    flats = engine.project(pieces, cam, scale, engine.YAW)

    tris_path = opt(av, '--tris')
    if tris_path:
        buf = bytearray(HEAD.pack(MAGIC, engine.W, engine.H, ntri))
        for f in flats:
            for (ax, ay, az, bx, by, bz, cx, cy, cz, colb) in f:
                buf += PACK.pack(ax, ay, az, bx, by, bz, cx, cy, cz, colb[0], colb[1], colb[2])
        with _open_w(tris_path) as fh:
            fh.write(bytes(buf))
        print('%s : %dx%d, %d triangles, %.1f Mo'
              % (tris_path, engine.W, engine.H, ntri, len(buf) / 1048576), file=sys.stderr)

    t1 = time.time()
    cbuf = bytearray(engine.BG)
    zbuf = array('f', engine.ZCLEAR)
    for f in flats:
        engine.draw(cbuf, zbuf, f)
    print('rasterisation %.1fs' % (time.time() - t1), file=sys.stderr)

    ppm = engine.HDR + bytes(cbuf)
    ppm_path = opt(av, '--ppm')
    if ppm_path:
        with _open_w(ppm_path) as fh:
            fh.write(ppm)
        print('%s : %dx%d, %.1f Mo' % (ppm_path, engine.W, engine.H, os.path.getsize(ppm_path) / 1048576),
              file=sys.stderr)
    elif not tris_path:
        sys.stdout.buffer.write(ppm)

    # l'empreinte se compare d'une machine a l'autre sans transferer l'image
    print('sha256 %s' % hashlib.sha256(ppm).hexdigest(), file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
