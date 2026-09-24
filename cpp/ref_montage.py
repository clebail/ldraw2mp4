#!/usr/bin/env python3
"""References de parite pour le montage : des images choisies du film.

    ref_montage.py <model.mpd> --count
    ref_montage.py <model.mpd> --frames 0,3,1200 [--prefix ref/walle_m]
                   [--piles N] [--pile-r F] [--pile-sig F] [--pile-h F]

Passe par `walle3.setup()` / `walle3.frame()` -- les memes fonctions que le
rendu reel, pas une copie. Une image du montage depend de beaucoup plus que
l'image fixe : le planning des rotations, le tirage des tas (Mersenne Twister de
CPython), le zoom, l'arc de vol et le tampon de progression. Chacun peut etre
faux tout seul, d'ou le choix d'images a des moments differents du film.
"""
import sys, os, gzip, contextlib, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'src'))
import walle3                                                   # noqa: E402
import engine                                                   # noqa: E402


@contextlib.contextmanager
def _open_w(path):
    raw = open(path, 'wb')
    try:
        if path.endswith('.gz'):
            with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as gz:
                yield gz
        else:
            yield raw
    finally:
        raw.close()


def opt(av, name, default=None):
    return av[av.index(name) + 1] if name in av else default


def main():
    av = sys.argv[1:]
    if not av:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    walle3.configure(av)                      # --piles / --pile-r / --pile-sig / --pile-h
    st = walle3.setup(av[0])
    T = walle3.nframes(st)
    print('%d pieces, %d images' % (walle3.npieces(st), T), file=sys.stderr)
    if '--count' in av:
        print(T)
        return 0
    prefix = opt(av, '--prefix', 'ref/montage_')
    for tok in opt(av, '--frames', '0').split(','):
        idx = int(tok)
        cb = walle3.frame(st, idx)
        ppm = engine.HDR + bytes(cb)
        path = '%s%d.ppm.gz' % (prefix, idx)
        with _open_w(path) as fh:
            fh.write(ppm)
        print('%s  sha256 %s' % (path, hashlib.sha256(ppm).hexdigest()), file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
