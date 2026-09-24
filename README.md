# ldraw2mp4

Transforme un modèle LEGO au format LDraw (`.ldr`, `.mpd`, par exemple un set
de l'[OMR](https://library.ldraw.org/omr)) en **vidéo de montage** : les pièces
arrivent une à une, la caméra suit, puis le modèle fini fait un tour complet.

▶️ **Exemple** : le Faucon Millénium UCS (5 605 pièces) monté par le binaire C++
— <https://youtu.be/zbDQ4wteWpw>

Moteur maison, sans moteur 3D ni GPU : parsing LDraw, rasterisation z-buffer,
animation. `ffmpeg` se charge uniquement de l'encodage.

- **`cpp/`** : le moteur en C++17, rapide et sobre en mémoire. C'est lui qu'il
  faut utiliser pour les gros modèles.
- **`src/`** : la version de référence en Python pur, sans aucune dépendance.
  Les deux produisent des images identiques au pixel près.

## Prérequis

- `ffmpeg`
- un compilateur C++17 (pour `cpp/`) et/ou Python 3.8+ (pour `src/`)
- la **bibliothèque de pièces LDraw**, non incluse (archive de 145 Mo, 615 Mo
  décompressés) :

```bash
curl -L -o complete.zip https://library.ldraw.org/library/updates/complete.zip
unzip -q complete.zip -d lib          # -> lib/ldraw/{parts,p,models}
```

## Utilisation

### Binaire C++

Depuis la racine du dépôt :

```bash
make -C cpp port                      # compile cpp/build/lego

L="cpp/build/lego models/omr/21303-walle.mpd --lib lib/ldraw"
$L --still | ffmpeg -y -i - -frames:v 1 out.png         # image fixe
$L --montage --count                                    # nombre d'images
$L --montage | ffmpeg -y -f image2pipe -vcodec ppm -framerate 30 -i - \
  -vf "scale=960:-2:flags=lanczos,format=yuv420p" -c:v libx264 -crf 20 walle.mp4
```

Le binaire écrit un flux PPM sur stdout. `--range A B` ne rend qu'une tranche
d'images, pour paralléliser sur plusieurs processus. `cpp/build/lego` sans
argument affiche toutes les options.

| Option | Défaut | Effet |
|---|---|---|
| `--size WxH` | `1440x960` | résolution du rendu |
| `--yaw DEG` | 35 | angle de vue initial |
| `--mode tas\|notice` | `tas` | `tas` : les pièces partent de tas disposés autour du modèle ; `notice` : chaque sous-modèle du `.mpd` est monté à l'écart, puis posé sur le modèle |
| `--piles N` | 7 | nombre de tas ; `0` = les pièces tombent à la verticale de leur place |
| `--zoom progress\|fit` | `progress` | `fit` : le cadrage suit la construction |
| `--fly N`, `--settle N` | 6, 1 | images de vol et de pose d'une pièce |
| `--rot N` | 12 | images d'une rotation de caméra |
| `--spin N` | 120 | images du tour final |
| `--transfer N`, `--hold N` | 18, 12 | `notice` : vol d'un sous-modèle, pause après sa pose |

### Scripts Python

Depuis `src/`. La bibliothèque est cherchée dans `../lib/ldraw`.

```bash
python3 walle3.py model.mpd --parallel 8 --out film.mp4    # montage complet, N processus
python3 walle3.py model.mpd --range 0 100 --out part.mp4   # une tranche
python3 layers_zoom.py model.ldr --parallel 8 --out film.mp4
python3 layers.py model.ldr | ffmpeg -y -f image2pipe -vcodec ppm -framerate 30 -i - \
  -vf "scale=960:-2:flags=lanczos,format=yuv420p" -c:v libx264 -crf 20 film.mp4
python3 sweep.py model.ldr 0,90,180,270                    # planche du modèle sous 4 angles
```

`walle3.py` accepte les mêmes options de mise en scène que le binaire C++.
`layers.py` et `layers_zoom.py` montent le modèle **couche par couche** : ils
attendent des métas `0 layer N` (non standard, produits par
[m3d](https://github.com/clebail/m3d)) et acceptent `--endyaw DEG` pour finir le
tour sur un angle donné.

## Contenu

```
cpp/
  lego.cpp          le moteur complet : .ldr/.mpd -> PPM, image fixe ou montage
  Makefile          compilation et tests de parité avec le Python
  ref/              images de référence (PPM gzippés)
  ...               outils de test (références Python, tirage aléatoire)
src/
  ldrawlib.py       parseur LDraw : .mpd, références récursives, couleurs
  engine.py         projection et rasterisation z-buffer
  walle3.py         montage : tas de départ, caméra automatique, zoom, rendu parallèle
  layers.py         montage couche par couche, caméra fixe
  layers_zoom.py    montage couche par couche, zoom qui suit la construction
  sweep.py          planche du modèle fini sous plusieurs angles
  cmp_ppm.py        comparaison de deux PPM au pixel près
  walle.py, walle2.py   versions précédentes du montage (caméra fixe, puis automatique)
  render.py, maison.py  premiers moteurs, sur des modèles générés par code
models/
  omr/              deux sets officiels : 21303 WALL·E, 10179 UCS Millennium Falcon
  canard.ldr, maison.ldr
```

## Tests

Le C++ est validé contre le Python, au pixel près :

```bash
make -C cpp check            # image fixe, WALL·E
make -C cpp check-montage    # images témoins du montage
make -C cpp help             # toutes les cibles
```

## Licence

Code sous licence [MIT](LICENSE). Les modèles de `models/omr/` restent sous
CCAL 2.0 et sont redistribués avec attribution : voir [CREDITS.md](CREDITS.md). LEGO® est une marque du groupe
LEGO, qui ne soutient pas ce projet.
