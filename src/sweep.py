#!/usr/bin/env python3
"""Planche de contact : le modele fini vu sous N angles de yaw.

    python3 sweep.py <model.ldr> 0,45,90,135,180,225,270,315
    python3 sweep.py <model.ldr> 330,345,0,15,30 --out ../previews/x_fine.png

Sert a choisir `--endyaw` (cf. README §3). Le rendu se fait en 300x534 : a
cette taille une planche de 8 angles coute quelques minutes au lieu d'une heure,
et la question posee -- « ou est le visage » -- se tranche tres bien en petit.

Sans `--out`, le nom se deduit du modele : model.ldr -> previews/model_sweep.png.
Pur Python comme le reste : la planche est assemblee en memoire, les legendes
dessinees avec une police bitmap 5x7, et ffmpeg ecrit le PNG (`.ppm` en sortie
l'evite).

Piege : `pts` contient les 8 coins de la boite de CHAQUE piece, pas un point par
piece. `frame_fit(pts, upto, ...)` prend un nombre de POINTS -- passer len(pieces)
ne cadre que sur le fond du modele et coupe le haut.
"""
import sys, os, math, subprocess
from array import array

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import engine

W, H, FAR = 300, 534, 1e30
engine.W, engine.H = W, H
engine.HDR = b'P6\n%d %d\n255\n' % (W, H)
engine.ZCLEAR = array('f', [FAR]) * (W * H)
_bg = bytearray(W * H * 3)
for _y in range(H):
    _t = _y / H
    _bg[_y * W * 3:(_y + 1) * W * 3] = bytes((int(16 + 14 * _t), int(19 + 17 * _t),
                                              int(26 + 22 * _t))) * W
engine.BG = _bg
engine.PITCH = math.radians(22)

import layers_zoom                                                   # noqa: E402

# Police bitmap 5x7, limitee a ce que les legendes affichent : « 270 deg »
FONT = {
    '0': ('01110', '10001', '10011', '10101', '11001', '10001', '01110'),
    '1': ('00100', '01100', '00100', '00100', '00100', '00100', '01110'),
    '2': ('01110', '10001', '00001', '00010', '00100', '01000', '11111'),
    '3': ('11110', '00001', '00001', '01110', '00001', '00001', '11110'),
    '4': ('00010', '00110', '01010', '10010', '11111', '00010', '00010'),
    '5': ('11111', '10000', '11110', '00001', '00001', '10001', '01110'),
    '6': ('00110', '01000', '10000', '11110', '10001', '10001', '01110'),
    '7': ('11111', '00001', '00010', '00100', '01000', '01000', '01000'),
    '8': ('01110', '10001', '10001', '01110', '10001', '10001', '01110'),
    '9': ('01110', '10001', '10001', '01111', '00001', '00010', '01100'),
    '-': ('00000', '00000', '00000', '11111', '00000', '00000', '00000'),
    'd': ('00001', '00001', '01101', '10011', '10001', '10011', '01101'),
    'e': ('00000', '00000', '01110', '10001', '11111', '10000', '01110'),
    'g': ('00000', '01111', '10001', '10001', '01111', '00001', '01110'),
    ' ': ('00000',) * 7,
}
PX = 3                                          # un pixel de police = 3x3 pixels


def text(buf, bw, x, y, s, rgb):
    """Ecrit `s` dans le buffer RGB `buf` (largeur `bw`), coin haut gauche en (x, y)."""
    px = bytes(rgb) * PX
    for c in s:
        for r, row in enumerate(FONT[c]):
            for k, bit in enumerate(row):
                if bit == '1':
                    for dy in range(PX):
                        o = ((y + r * PX + dy) * bw + x + k * PX) * 3
                        buf[o:o + 3 * PX] = px
        x += 6 * PX


def sheet(ims):
    """Planche de contact des images (deg, rgb W x H) -> (octets RGB, largeur, hauteur)."""
    cols = len(ims) if len(ims) <= 6 else (len(ims) + 1) // 2
    rows = (len(ims) + cols - 1) // cols
    sw, sh = cols * W, rows * (H + 34)
    buf = bytearray(bytes((12, 14, 20)) * (sw * sh))
    for i, (deg, im) in enumerate(ims):
        x, y = (i % cols) * W, (i // cols) * (H + 34)
        for r in range(H):
            o = ((y + r) * sw + x) * 3
            buf[o:o + W * 3] = im[r * W * 3:(r + 1) * W * 3]
        label = '%d deg' % deg
        text(buf, sw, x + (W - (6 * len(label) - 1) * PX) // 2, y + H + 6, label,
             (240, 200, 90))
    return buf, sw, sh


def save(out, buf, sw, sh):
    ppm = b'P6\n%d %d\n255\n' % (sw, sh) + bytes(buf)
    if out.lower().endswith('.ppm'):
        with open(out, 'wb') as f:
            f.write(ppm)
        return
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'image2pipe',
                    '-vcodec', 'ppm', '-i', '-', '-frames:v', '1', out],
                   input=ppm, check=True)


def main():
    path = sys.argv[1]
    angles = [int(a) for a in sys.argv[2].split(',')]
    if '--out' in sys.argv:
        out = sys.argv[sys.argv.index('--out') + 1]
    else:
        name = os.path.splitext(os.path.basename(path))[0]
        out = os.path.join(HERE, '..', 'previews', '%s_sweep.png' % name)
        os.makedirs(os.path.dirname(out), exist_ok=True)

    pieces, sizes, pts, lo, hi = layers_zoom.geometry(path)
    layers_zoom.HEAD = 0.0
    n = len(pieces)
    print('geometrie chargee : %d pieces, %d couches' % (n, len(sizes)), file=sys.stderr)

    ims = []
    for deg in angles:
        yaw = math.radians(deg)
        cam, scale = layers_zoom.frame_fit(pts, len(pts), yaw, 0.86, 0.0)
        cb, zb = bytearray(engine.BG), engine.ZCLEAR[:]
        layers_zoom.compose(cb, zb, pieces, n, n, layers_zoom.FALL, yaw, cam, scale)
        ims.append((deg, cb))
        print('  yaw %d ok' % deg, file=sys.stderr)

    save(out, *sheet(ims))
    print(out)


if __name__ == '__main__':
    main()
