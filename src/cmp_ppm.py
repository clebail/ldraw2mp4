#!/usr/bin/env python3
"""Compare deux images PPM binaires (P6) au pixel pres. Les `.gz` sont lus tels quels.

    cmp_ppm.py <a.ppm> <b.ppm> [--tol N] [--frame-a N] [--frame-b N]
               [--list K] [--diff out.ppm]

Outil de non-regression du portage C++ : la reference est le rendu Python, le
candidat la sortie du binaire. La cible est le PIXEL EXACT (--tol 0), pas une
tolerance -- on a verifie que meme en float le rastériseur ecrit exactement
les memes pixels qu'en double, donc tout ecart est un bug, pas un arrondi.

Un `-` en argument lit stdin, ce qui permet de brancher le binaire directement :

    ./falcon model.ldr --still | cmp_ppm.py previews/falcon.ppm -

Les producteurs du depot ecrivent un FLUX de plusieurs images sur stdout ;
--frame-a / --frame-b choisissent laquelle comparer (0 = la premiere).

Code de sortie : 0 si les images concordent, 1 sinon, 2 si elles ne sont meme
pas comparables (dimensions differentes, fichier tronque).
"""
import sys
import gzip

MAGENTA = b'\xff\x00\xff'


def _token(f):
    """Lit un token d'en-tete PPM : espaces ignorees, commentaires # jusqu'au bout de ligne."""
    tok = b''
    while True:
        c = f.read(1)
        if not c:
            raise ValueError('en-tete PPM tronque')
        if c == b'#':
            while c and c not in b'\r\n':
                c = f.read(1)
            continue
        if c.isspace():
            if tok:
                return tok
            continue
        tok += c


def read_ppm(path, frame=0):
    """Rend (largeur, hauteur, donnees) de l'image `frame` du flux."""
    if path == '-':
        f = sys.stdin.buffer
    else:
        # les references sont stockees gzippees : 4 Mo de PPM en pesent ~1
        f = gzip.open(path, 'rb') if path.endswith('.gz') else open(path, 'rb')
    try:
        for n in range(frame + 1):
            try:
                magic = _token(f)
            except ValueError:
                raise ValueError('%s : image %d absente, le flux en compte %d' % (path, frame, n))
            if magic != b'P6':
                raise ValueError('%s : format %s, seul le P6 binaire est gere' % (path, magic.decode()))
            w, h, maxv = (int(_token(f)) for _ in range(3))
            if maxv != 255:
                raise ValueError('%s : maxval %d, seul 255 est gere' % (path, maxv))
            data = f.read(w * h * 3)
            if len(data) != w * h * 3:
                raise ValueError('%s : image %d tronquee, %d octets sur %d'
                                 % (path, n, len(data), w * h * 3))
        return w, h, data
    finally:
        if f is not sys.stdin.buffer:
            f.close()


def compare(a, b, w, h, tol=0, list_k=8, diff=None):
    """Compare deux plans RGB de meme taille. Rend un dict de statistiques."""
    stride = w * 3
    ndiff = maxdev = 0
    buckets = [0, 0, 0, 0]                    # 1 / 2-4 / 5-16 / >16
    x0, y0, x1, y1 = w, h, -1, -1
    worst = None
    samples = []
    for y in range(h):
        o = y * stride
        ra, rb = a[o:o + stride], b[o:o + stride]
        if ra == rb:
            continue
        # ligne differente : on ne descend au pixel que la, le reste part a
        # vitesse memcmp -- une image quasi identique se compare en une seconde
        for x in range(w):
            i = x * 3
            pa, pb = ra[i:i + 3], rb[i:i + 3]
            if pa == pb:
                continue
            d = max(abs(pa[0] - pb[0]), abs(pa[1] - pb[1]), abs(pa[2] - pb[2]))
            if d <= tol:
                continue
            ndiff += 1
            if d > maxdev:
                maxdev, worst = d, (x, y, tuple(pa), tuple(pb))
            buckets[0 if d == 1 else 1 if d <= 4 else 2 if d <= 16 else 3] += 1
            if x < x0: x0 = x
            if x > x1: x1 = x
            if y < y0: y0 = y
            if y > y1: y1 = y
            if len(samples) < list_k:
                samples.append((x, y, tuple(pa), tuple(pb)))
            if diff is not None:
                diff[o + i:o + i + 3] = MAGENTA
    return {'ndiff': ndiff, 'maxdev': maxdev, 'buckets': buckets, 'worst': worst,
            'samples': samples, 'box': (x0, y0, x1, y1) if ndiff else None}


def main():
    av = sys.argv[1:]
    if len(av) < 2 or '-h' in av or '--help' in av:
        print(__doc__.strip(), file=sys.stderr)
        return 2

    def opt(name, default):
        return av[av.index(name) + 1] if name in av else default

    tol = int(opt('--tol', 0))
    list_k = int(opt('--list', 8))
    diff_path = opt('--diff', None)
    pa, pb = av[0], av[1]

    try:
        wa, ha, da = read_ppm(pa, int(opt('--frame-a', 0)))
        wb, hb, db = read_ppm(pb, int(opt('--frame-b', 0)))
    except (ValueError, OSError) as e:
        print('erreur : %s' % e, file=sys.stderr)
        return 2
    if (wa, ha) != (wb, hb):
        print('INCOMPARABLE  %s est %dx%d, %s est %dx%d' % (pa, wa, ha, pb, wb, hb))
        return 2

    npix = wa * ha
    # l'image de diff n'est allouee que si elle est demandee : 4 Mo par image
    diff = bytearray(da) if diff_path else None
    st = compare(da, db, wa, ha, tol, list_k, diff)

    if st['ndiff'] == 0:
        print('IDENTIQUE     %dx%d, %d pixels%s' % (wa, ha, npix, '' if tol == 0 else ', tol %d' % tol))
        return 0

    x0, y0, x1, y1 = st['box']
    b1, b4, b16, bmax = st['buckets']
    print('DIFFERENT     %dx%d' % (wa, ha))
    print('  pixels      %d sur %d  (%.4f %%)' % (st['ndiff'], npix, 100.0 * st['ndiff'] / npix))
    print('  ecart max   %d  sur un canal' % st['maxdev'])
    print('  repartition %d a 1, %d a 2-4, %d a 5-16, %d au-dela' % (b1, b4, b16, bmax))
    print('  zone        x %d..%d, y %d..%d  (%dx%d)' % (x0, x1, y0, y1, x1 - x0 + 1, y1 - y0 + 1))
    wx, wy, wa_, wb_ = st['worst']
    print('  pire pixel  (%d,%d)  %s vs %s' % (wx, wy, wa_, wb_))
    for (x, y, ca, cb) in st['samples']:
        print('    (%4d,%4d)  %s  vs  %s' % (x, y, ca, cb))
    if diff_path:
        with open(diff_path, 'wb') as f:
            f.write(b'P6\n%d %d\n255\n' % (wa, ha))
            f.write(bytes(diff))
        print('  diff        %s  (pixels fautifs en magenta)' % diff_path)
    return 1


if __name__ == '__main__':
    sys.exit(main())
