// Noyau de rasterisation partage par raster_ppm (etape 1) et lego (chaine
// complete). Transcription littérale de engine.draw(), garde-fous du README §5
// compris. Trois details de parite qui ne se voient pas a la lecture :
//   - l'arithmetique est en double, mais le tampon de profondeur en float :
//     `array('f')` cote Python. Un zbuf en double change les surfaces tangentes.
//   - le fond est le degrade d'engine.BG, pas du noir.
//   - les bornes `(int)(x + 0.5)` tronquent vers zero des deux cotes.
#pragma once
#include <cstdint>
#include <cstring>
#include <vector>
#include <algorithm>

static const float FAR_Z = 1e30f;

#pragma pack(push, 1)
struct Head { char magic[8]; uint32_t w, h; uint64_t n; };   // dump LDRTRIS1
struct Tri  { double v[9]; uint8_t col[3]; };                // 75 octets
#pragma pack(pop)
static_assert(sizeof(Head) == 24, "en-tete non packee");
static_assert(sizeof(Tri) == 75, "triangle non packe");

inline void background(std::vector<uint8_t>& cbuf, int W, int H) {
    for (int y = 0; y < H; y++) {
        double t = (double)y / H;
        uint8_t px[3] = { (uint8_t)(int)(36 + 26 * t),
                          (uint8_t)(int)(42 + 30 * t),
                          (uint8_t)(int)(52 + 34 * t) };
        uint8_t* row = &cbuf[(size_t)y * W * 3];
        for (int x = 0; x < W; x++) memcpy(row + x * 3, px, 3);
    }
}

// engine.progress() : la barre de progression du montage
inline void progress(std::vector<uint8_t>& cbuf, double frac, int W, int H) {
    int x0 = (int)(W * 0.25), y0 = H - 46, w = (int)(W * 0.5), h = 8;
    int n = (int)(w * frac);
    for (int yy = y0; yy < y0 + h; yy++) {
        uint8_t* o = &cbuf[((size_t)yy * W + x0) * 3];
        for (int i = 0; i < w; i++) { o[i*3] = 0x3a; o[i*3+1] = 0x40; o[i*3+2] = 0x48; }
        for (int i = 0; i < n; i++) { o[i*3] = 0xff; o[i*3+1] = 0xd7; o[i*3+2] = 0x3c; }
    }
}

// Rasterise un lot de triangles deja projetes. `n` triangles a partir de `t`.
inline void draw(std::vector<uint8_t>& cbuf, std::vector<float>& zbuf,
                 const Tri* tris, size_t n, int W, int H,
                 double dx = 0.0, double dy = 0.0, double dz = 0.0) {
    for (size_t k = 0; k < n; k++) {
        double ax = tris[k].v[0], ay = tris[k].v[1], az = tris[k].v[2];
        double bx = tris[k].v[3], by = tris[k].v[4], bz = tris[k].v[5];
        double cx = tris[k].v[6], cy = tris[k].v[7], cz = tris[k].v[8];
        const uint8_t* colb = tris[k].col;
        // le decalage d'une piece qui tombe est applique AVANT le tri des
        // sommets, comme dans engine.draw()
        ax += dx; bx += dx; cx += dx;
        ay += dy; by += dy; cy += dy;
        az += dz; bz += dz; cz += dz;

        if (ay > by) { std::swap(ax,bx); std::swap(ay,by); std::swap(az,bz); }
        if (by > cy) { std::swap(bx,cx); std::swap(by,cy); std::swap(bz,cz); }
        if (ay > by) { std::swap(ax,bx); std::swap(ay,by); std::swap(az,bz); }

        int y0 = (int)(ay + 0.5), y1 = (int)(cy + 0.5);
        if (y1 < 0 || y0 > H) continue;
        if (y0 < 0) y0 = 0;
        if (y1 > H - 1) y1 = H - 1;

        double dac = cy - ay, dab = by - ay, dbc = cy - by;
        for (int y = y0; y <= y1; y++) {
            double yc = y + 0.5;
            // §5a : sur un triangle quasi horizontal, dac est minuscule et
            // l'interpolant non borne projetterait l'arete a l'autre bout
            double tt = dac > 1e-9 ? (yc - ay) / dac : 0.0;
            if (tt < 0.0) tt = 0.0; else if (tt > 1.0) tt = 1.0;
            double xl = ax + tt * (cx - ax), zl = az + tt * (cz - az);
            double xr, zr;
            if (yc < by) {
                double t2 = dab > 1e-9 ? (yc - ay) / dab : 0.0;
                if (t2 < 0.0) t2 = 0.0; else if (t2 > 1.0) t2 = 1.0;
                xr = ax + t2 * (bx - ax); zr = az + t2 * (bz - az);
            } else {
                double t2 = dbc > 1e-9 ? (yc - by) / dbc : 0.0;
                if (t2 < 0.0) t2 = 0.0; else if (t2 > 1.0) t2 = 1.0;
                xr = bx + t2 * (cx - bx); zr = bz + t2 * (cz - bz);
            }
            if (xl > xr) { std::swap(xl,xr); std::swap(zl,zr); }
            int i0 = (int)(xl + 0.5), i1 = (int)(xr + 0.5);
            if (i1 < 0 || i0 > W - 1) continue;
            double span = xr - xl;
            if (i0 < 0) i0 = 0;
            if (i1 > W - 1) i1 = W - 1;
            double slope, z;
            if (span > 1.0) {
                slope = (zr - zl) / span;
                z = zl + (i0 + 0.5 - xl) * slope;
            } else {
                // §5b : sur un span sous-pixel la pente exploserait et le z
                // extrapole serait aberrant -> profondeur moyenne
                slope = 0.0;
                z = (zl + zr) * 0.5;
            }
            size_t idx = (size_t)y * W + i0;
            for (int i = i0; i <= i1; i++) {
                if (z < zbuf[idx]) {
                    zbuf[idx] = (float)z;
                    memcpy(&cbuf[idx * 3], colb, 3);
                }
                z += slope; idx++;
            }
        }
    }
}
