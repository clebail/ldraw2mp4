// Etape 1 du portage : le rastériseur seul, sans parseur.
//
//     raster_ppm <tris.bin> [--reps N] > out.ppm
//
// Lit les triangles DEJA projetes produits par `ref_still.py --tris` et ecrit
// le PPM sur stdout. Comme le Python a produit l'image de reference a partir
// exactement de ces triangles, tout ecart de pixel accuse le rastériseur, et
// lui seul : aucun parsing, aucune projection, aucune camera en jeu.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <string>
#include <vector>
#include <chrono>
#include <algorithm>
#include "raster_core.h"

int main(int argc, char** argv) {
    if (argc < 2) { fprintf(stderr, "usage: raster_ppm <tris.bin> [--reps N] > out.ppm\n"); return 2; }
    int reps = 1;
    for (int i = 2; i < argc - 1; i++) if (!strcmp(argv[i], "--reps")) reps = atoi(argv[i + 1]);

    FILE* f = fopen(argv[1], "rb");
    if (!f) { fprintf(stderr, "%s illisible\n", argv[1]); return 2; }
    Head hd;
    if (fread(&hd, sizeof hd, 1, f) != 1 || memcmp(hd.magic, "LDRTRIS1", 8)) {
        fprintf(stderr, "%s : pas un dump LDRTRIS1\n", argv[1]); return 2;
    }
    int W = (int)hd.w, H = (int)hd.h;
    std::vector<Tri> tris(hd.n);
    if (fread(tris.data(), sizeof(Tri), hd.n, f) != hd.n) {
        fprintf(stderr, "%s : dump tronque\n", argv[1]); return 2;
    }
    fclose(f);
    fprintf(stderr, "%s : %dx%d, %llu triangles\n", argv[1], W, H, (unsigned long long)hd.n);

    std::vector<uint8_t> cbuf((size_t)W * H * 3);
    std::vector<float> zbuf((size_t)W * H);
    double best = 1e18;
    for (int r = 0; r < reps; r++) {
        background(cbuf, W, H);
        std::fill(zbuf.begin(), zbuf.end(), FAR_Z);
        auto t0 = std::chrono::steady_clock::now();
        draw(cbuf, zbuf, tris.data(), tris.size(), W, H);
        double s = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
        fprintf(stderr, "  passe %d : %.4f s\n", r, s);
        if (s < best) best = s;
    }

    printf("P6\n%d %d\n255\n", W, H);
    fwrite(cbuf.data(), 1, cbuf.size(), stdout);
    fflush(stdout);
    return 0;
}
