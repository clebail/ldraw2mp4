// Etape 2 du portage : la chaine complete, du .ldr au PPM.
//
//     lego <model.ldr|mpd> [--lib DIR] [--yaw DEG] [--size WxH] [--margin F]
//          [--tris out.bin] [--still]
//
// Transcription de ldrawlib.py + engine.load/make_cam/project. La cible n'est
// pas « une image qui ressemble » mais le PIXEL EXACT, et surtout le DUMP EXACT :
// `--tris` ecrit les memes octets que `ref_still.py --tris`, ce qui fait echouer
// un parseur faux a un offset precis au lieu de lui laisser rendre une image
// plausible.
//
// Quatre pieges de parite, tous silencieux :
//
//  1. FMA. `a*b + c*d` contracte en fused-multiply-add change le dernier bit.
//     Compile avec -ffp-contract=off (cf. Makefile) ; sans lui le dump diverge
//     alors que l'image, elle, passerait.
//  2. radians(). CPython fait `x * (pi/180)`, pas `(x*pi)/180` : un ulp d'ecart
//     sur l'angle de camera deplace des sommets entiers.
//  3. L'ordre d'indexation de la bibliotheque. Python fait os.walk : le
//     repertoire courant AVANT ses sous-repertoires, et parts/ avant p/ avant
//     models/. C'est ce qui fait gagner parts/3001.dat sur parts/s/3001.dat.
//     417 basenames sont en collision dans la bibliotheque complete ; un seul
//     (t04i4000.dat, entre p/8/ et p/48/) est tranche par l'ordre de readdir,
//     d'ou le parcours a l'identique plutot qu'un tri.
//  4. int(x) tronque vers zero. Pour l'eclairage `int(ch * k)` c'est un plancher,
//     pas un arrondi.
//  5. round() de Python arrondit au PAIR sur les demis, std::round arrondit a
//     l'oppose de zero. Le planning de camera quantifie un angle avec round().
//  6. `%` de Python sur des flottants n'est pas fmod : le reste prend le signe
//     du DIVISEUR. Le planning s'en sert pour ramener un ecart d'angle dans
//     [-pi, pi] ; avec fmod, une rotation sur deux part du mauvais cote.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <cmath>
#include <string>
#include <string_view>
#include <vector>
#include <unordered_map>
#include <chrono>
#include <array>
#include <dirent.h>
#include <sys/stat.h>
#include "raster_core.h"
#include "pyrandom.h"

// --- constantes d'engine.py -------------------------------------------------

static const double DEG2RAD = M_PI / 180.0;          // piege 2
static inline double radians(double d) { return d * DEG2RAD; }

static int W = 1440, H = 960;
static double YAW = radians(35), PITCH = radians(24);
static const double LX = -0.42, LY = 0.80, LZ = 0.43;

// --- utilitaires ------------------------------------------------------------

static std::string lower(std::string s) {
    for (char& c : s) if (c >= 'A' && c <= 'Z') c += 32;
    return s;
}

static std::vector<std::string_view> split(std::string_view s) {
    std::vector<std::string_view> out;
    size_t i = 0;
    while (i < s.size()) {
        while (i < s.size() && (unsigned char)s[i] <= ' ') i++;
        size_t j = i;
        while (j < s.size() && (unsigned char)s[j] > ' ') j++;
        if (j > i) out.push_back(s.substr(i, j - i));
        i = j;
    }
    return out;
}

// join(' ') des tokens a partir de `from`, comme ' '.join(t[14:])
static std::string join(const std::vector<std::string_view>& t, size_t from) {
    std::string s;
    for (size_t i = from; i < t.size(); i++) { if (i > from) s += ' '; s += t[i]; }
    return s;
}

// int() / float() de Python : le token doit etre consomme en entier, sinon
// ValueError et la ligne est ignoree
static bool to_int(std::string_view tok, int* out) {
    std::string s(tok);
    char* end; errno = 0;
    long v = strtol(s.c_str(), &end, 10);
    if (end != s.c_str() + s.size() || end == s.c_str()) return false;
    *out = (int)v;
    return true;
}
static bool to_double(std::string_view tok, double* out) {
    std::string s(tok);
    char* end;
    double v = strtod(s.c_str(), &end);
    if (end != s.c_str() + s.size() || end == s.c_str()) return false;
    *out = v;
    return true;
}

static bool read_file(const std::string& path, std::string* out) {
    FILE* f = fopen(path.c_str(), "rb");
    if (!f) return false;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    out->resize(n > 0 ? n : 0);
    if (n > 0 && fread(&(*out)[0], 1, n, f) != (size_t)n) { fclose(f); return false; }
    fclose(f);
    return true;
}

static std::vector<std::string_view> lines_of(const std::string& s) {
    std::vector<std::string_view> out;
    size_t i = 0;
    while (i <= s.size()) {
        size_t j = s.find('\n', i);
        if (j == std::string::npos) { if (i < s.size()) out.push_back(std::string_view(s).substr(i)); break; }
        out.push_back(std::string_view(s).substr(i, j - i));
        i = j + 1;
    }
    return out;
}

static std::string basename_of(const std::string& k) {
    size_t p = k.rfind('/');
    return p == std::string::npos ? k : k.substr(p + 1);
}

// --- la bibliotheque LDraw --------------------------------------------------

struct Tri3 { double p[9]; int colour; };
struct RGB  { uint8_t r, g, b; };

struct Lib {
    std::unordered_map<std::string, std::string> files;     // cle -> chemin
    std::unordered_map<int, RGB> colours;
    std::unordered_map<std::string, std::vector<Tri3>> cache;

    void index(const std::string& root) {
        for (const char* sub : {"parts", "p", "models"}) {
            std::string base = root + "/" + sub;
            struct stat st;
            if (stat(base.c_str(), &st) || !S_ISDIR(st.st_mode)) continue;
            walk(base, base, "");
        }
        load_colours(root);
    }

    // os.walk top-down : les fichiers du repertoire courant d'abord, puis les
    // sous-repertoires dans l'ordre de readdir (piege 3)
    void walk(const std::string& base, const std::string& dir, const std::string& rel) {
        DIR* d = opendir(dir.c_str());
        if (!d) return;
        std::vector<std::string> subdirs;
        struct dirent* e;
        while ((e = readdir(d))) {
            std::string n = e->d_name;
            if (n == "." || n == "..") continue;
            std::string full = dir + "/" + n;
            bool isdir;
            if (e->d_type == DT_DIR)       isdir = true;
            else if (e->d_type == DT_UNKNOWN) { struct stat st; isdir = !stat(full.c_str(), &st) && S_ISDIR(st.st_mode); }
            else                            isdir = false;
            if (isdir) { subdirs.push_back(n); continue; }
            std::string ln = lower(n);
            if (ln.size() < 4) continue;
            std::string ext = ln.substr(ln.size() - 4);
            if (ext != ".dat" && ext != ".ldr") continue;
            if (!rel.empty()) files.emplace(rel + "/" + ln, full);
            files.emplace(ln, full);
        }
        closedir(d);
        for (const std::string& n : subdirs)
            walk(base, dir + "/" + n, rel.empty() ? lower(n) : rel + "/" + lower(n));
    }

    void load_colours(const std::string& root) {
        std::string txt;
        if (!read_file(root + "/LDConfig.ldr", &txt)) return;
        for (std::string_view ln : lines_of(txt)) {
            auto t = split(ln);
            if (t.size() < 4 || t[1] != "!COLOUR") continue;
            size_t ic = t.size(), iv = t.size();
            for (size_t i = 0; i < t.size(); i++) {
                if (ic == t.size() && t[i] == "CODE")  ic = i;
                if (iv == t.size() && t[i] == "VALUE") iv = i;
            }
            if (ic + 1 >= t.size() || iv + 1 >= t.size()) continue;
            int code;
            if (!to_int(t[ic + 1], &code)) continue;
            std::string v(t[iv + 1]);
            if (!v.empty() && v[0] == '#') v.erase(0, 1);
            if (v.size() < 6) continue;
            int ch[3];
            bool ok = true;
            for (int j = 0; j < 3 && ok; j++) {
                std::string pair = v.substr(j * 2, 2);
                char* end;
                long x = strtol(pair.c_str(), &end, 16);
                if (end != pair.c_str() + 2) ok = false; else ch[j] = (int)x;
            }
            if (!ok) continue;
            colours.emplace(code, RGB{(uint8_t)ch[0], (uint8_t)ch[1], (uint8_t)ch[2]});
        }
    }

    RGB rgb(int code) const {
        auto it = colours.find(code);
        return it == colours.end() ? RGB{140, 140, 140} : it->second;
    }

    const std::vector<Tri3>& geom(const std::string& name) {
        std::string key = lower(name);
        for (char& c : key) if (c == '\\') c = '/';
        auto hit = cache.find(key);
        if (hit != cache.end()) return hit->second;
        cache[key];                                      // garde-fou anti-recursion

        auto f = files.find(key);
        if (f == files.end()) f = files.find(basename_of(key));
        if (f == files.end()) return cache[key];
        std::string txt;
        if (!read_file(f->second, &txt)) return cache[key];

        std::vector<Tri3> tris;
        for (std::string_view line : lines_of(txt)) {
            auto t = split(line);
            if (t.size() < 2) continue;
            if (t[0] == "1" && t.size() >= 15) {
                int c; double m[12];
                if (!to_int(t[1], &c)) continue;
                bool ok = true;
                for (int j = 0; j < 12 && ok; j++) ok = to_double(t[2 + j], &m[j]);
                if (!ok) continue;
                double x = m[0], y = m[1], z = m[2];
                double a = m[3], b = m[4], cc = m[5];
                double d = m[6], e = m[7], ff = m[8];
                double g = m[9], h = m[10], i = m[11];
                const std::vector<Tri3>& sub = geom(join(t, 14));
                for (const Tri3& s : sub) {
                    Tri3 o;
                    for (int k = 0; k < 3; k++) {
                        double px = s.p[k*3], py = s.p[k*3+1], pz = s.p[k*3+2];
                        o.p[k*3]   = a * px + b * py + cc * pz + x;
                        o.p[k*3+1] = d * px + e * py + ff * pz + y;
                        o.p[k*3+2] = g * px + h * py + i  * pz + z;
                    }
                    o.colour = (s.colour == 16) ? c : s.colour;
                    tris.push_back(o);
                }
            } else if (t[0] == "3" || t[0] == "4") {
                int n = t[0] == "3" ? 9 : 12;
                if ((int)t.size() < 2 + n) continue;
                int c;
                if (!to_int(t[1], &c)) continue;
                double v[12];
                bool ok = true;
                for (int j = 0; j < n && ok; j++) ok = to_double(t[2 + j], &v[j]);
                if (!ok) continue;
                if (c == 24) continue;                   // couleur de bord
                Tri3 o; o.colour = c;
                memcpy(o.p, v, 9 * sizeof(double));
                tris.push_back(o);
                if (n == 12) {                           // quad -> deux triangles
                    Tri3 q; q.colour = c;
                    memcpy(q.p,     v,     3 * sizeof(double));
                    memcpy(q.p + 3, v + 6, 3 * sizeof(double));
                    memcpy(q.p + 6, v + 9, 3 * sizeof(double));
                    tris.push_back(q);
                }
            }
        }
        return cache[key] = std::move(tris);
    }
};

// --- le modele --------------------------------------------------------------

struct Placement { std::string ref; int colour; double M[12]; };

// load_mpd : decoupe en {nom minuscule: lignes}, plus le nom du principal
static std::string load_mpd(const std::string& txt,
                            std::unordered_map<std::string, std::vector<std::string>>* files) {
    std::string first;
    std::string cur;
    bool have = false;
    for (std::string_view raw : lines_of(txt)) {
        std::string s(raw);
        while (!s.empty() && (unsigned char)s.back() <= ' ') s.pop_back();
        size_t b = 0; while (b < s.size() && (unsigned char)s[b] <= ' ') b++;
        s.erase(0, b);
        std::string low = lower(s);
        if (low.rfind("0 file ", 0) == 0) {
            cur = low.substr(7);
            while (!cur.empty() && (unsigned char)cur.back() <= ' ') cur.pop_back();
            size_t c0 = 0; while (c0 < cur.size() && (unsigned char)cur[c0] <= ' ') c0++;
            cur.erase(0, c0);
            (*files)[cur];
            if (!have) { first = cur; have = true; }
        } else if (!have) {
            cur = "__main__";
            (*files)[cur].push_back(s);
            first = cur; have = true;
        } else {
            (*files)[cur].push_back(s);
        }
    }
    return first;
}

static void compose(const double* P, const double* C, double* out) {
    double a=P[0],b=P[1],c=P[2],d=P[3],e=P[4],f=P[5],g=P[6],h=P[7],i=P[8],tx=P[9],ty=P[10],tz=P[11];
    double A=C[0],B=C[1],C_=C[2],D=C[3],E=C[4],F=C[5],G=C[6],I_=C[7],I=C[8],TX=C[9],TY=C[10],TZ=C[11];
    out[0]=a*A+b*D+c*G; out[1]=a*B+b*E+c*I_; out[2]=a*C_+b*F+c*I;
    out[3]=d*A+e*D+f*G; out[4]=d*B+e*E+f*I_; out[5]=d*C_+e*F+f*I;
    out[6]=g*A+h*D+i*G; out[7]=g*B+h*E+i*I_; out[8]=g*C_+h*F+i*I;
    out[9]  = a*TX+b*TY+c*TZ+tx;
    out[10] = d*TX+e*TY+f*TZ+ty;
    out[11] = g*TX+h*TY+i*TZ+tz;
}

static void placements(const std::unordered_map<std::string, std::vector<std::string>>& files,
                       const std::string& name, const double* xform, int colour,
                       std::vector<Placement>* out, int depth) {
    if (depth > 32) return;
    auto it = files.find(name);
    if (it == files.end()) return;
    for (const std::string& ln : it->second) {
        auto t = split(ln);
        if (t.size() < 15 || t[0] != "1") continue;
        int c; double m[12];
        if (!to_int(t[1], &c)) continue;
        bool ok = true;
        for (int j = 0; j < 12 && ok; j++) ok = to_double(t[2 + j], &m[j]);
        if (!ok) continue;
        std::string ref = lower(join(t, 14));
        for (char& ch : ref) if (ch == '\\') ch = '/';
        double local[12] = { m[3],m[4],m[5], m[6],m[7],m[8], m[9],m[10],m[11], m[0],m[1],m[2] };
        double M[12];
        compose(xform, local, M);
        int col = (c == 16) ? colour : c;
        if (files.count(ref)) placements(files, ref, M, col, out, depth + 1);
        else { Placement p; p.ref = ref; p.colour = col; memcpy(p.M, M, sizeof M); out->push_back(p); }
    }
}

// --- constantes de walle3.py ------------------------------------------------

// cadencement : images par vol, par temps mort, par rotation, par tour. C'est
// ce qui fixe la duree du film.
static int FLY = 6, SETTLE = 1, ROT = 12, SPIN = 120;
static const int SLOTS = 12;
static const double BACK = 0.12, MINR = 0.30;
// reglables : cf. walle3.configure(), les memes options des deux cotes
static int NPILES = 7;
static double PILE_R = 1.50, PILE_SIG = 0.155, PILE_H = 0.20;
static double DROP = 0.35;             // --piles 0 : hauteur de chute, en R
static bool ZOOM_FIT = false;          // --zoom fit : le champ suit la construction
static double ZOOM_MIN = 0.35, HEAD = 0.20;
static bool NOTICE = false;            // --mode notice : montage en notice animee
// la camera tourne pour degager le point d'accroche de la piece qui arrive,
// comme schedule() en mode tas -- d'ou plus d'orbite automatique
static double STAGE_R = 1.25;
static int TRANSFER = 18, HOLD = 12;
static const double MARGIN = 0.90;
static const double ARC = 0.55;
static const uint32_t SEED = 20213;
static const double ZOOM_END = 0.85;

// piege 5 : float.__round__ de CPython, demis au pair
static double pyround(double x) {
    double r = round(x);
    if (fabs(x - r) == 0.5) r = 2.0 * round(x / 2.0);
    return r;
}

// piege 6 : le reste prend le signe du diviseur, contrairement a fmod
static double pymod(double a, double b) {
    double m = fmod(a, b);
    if (m != 0.0) { if ((b < 0) != (m < 0)) m += b; }
    else m = copysign(0.0, b);
    return m;
}

// --- camera -----------------------------------------------------------------

struct Cam { double c[3]; double scale; };

static Cam make_cam(const double* lo, const double* hi, double margin) {
    Cam k;
    for (int j = 0; j < 3; j++) k.c[j] = (lo[j] + hi[j]) / 2;
    double cp = cos(PITCH), sp = sin(PITCH);
    double ex = 0.0, ey = 0.0;
    for (int i = -1; i < 16; i++) {
        double yaw = i < 0 ? YAW : 2 * M_PI * i / 16;
        double cyw = cos(yaw), syw = sin(yaw);
        for (int u = 0; u < 2; u++) for (int v = 0; v < 2; v++) for (int w = 0; w < 2; w++) {
            double ax = (u ? hi[0] : lo[0]) - k.c[0], az = (w ? hi[2] : lo[2]) - k.c[2];
            ex = std::max(ex, fabs(ax * cyw - az * syw));
            ey = std::max(ey, fabs(((v ? hi[1] : lo[1]) - k.c[1]) * cp + (ax * syw + az * cyw) * sp));
        }
    }
    k.scale = std::min(W * margin / (2 * ex), H * margin / (2 * ey));
    return k;
}

// engine.fit_scale : 16 angles seulement, et les extremes partent de 1e-6
static double fit_scale(const double* cam, const double* lo, const double* hi, double margin) {
    double cp = cos(PITCH), sp = sin(PITCH);
    double ex = 1e-6, ey = 1e-6;
    for (int i = 0; i < 16; i++) {
        double yaw = 2 * M_PI * i / 16;
        double cyw = cos(yaw), syw = sin(yaw);
        for (int u = 0; u < 2; u++) for (int v = 0; v < 2; v++) for (int w = 0; w < 2; w++) {
            double ax = (u ? hi[0] : lo[0]) - cam[0], az = (w ? hi[2] : lo[2]) - cam[2];
            ex = std::max(ex, fabs(ax * cyw - az * syw));
            ey = std::max(ey, fabs(((v ? hi[1] : lo[1]) - cam[1]) * cp + (ax * syw + az * cyw) * sp));
        }
    }
    return std::min(W * margin / (2 * ex), H * margin / (2 * ey));
}

static void project_piece(const std::vector<Tri>& src, const double* cam, double scale,
                          double cyw, double syw, double cp, double sp, std::vector<Tri>* out) {
    out->clear(); out->reserve(src.size());
    double ox = W * 0.5, oy = H * 0.5;
    for (const Tri& s : src) {
        Tri o;
        for (int k = 0; k < 3; k++) {
            double ax = s.v[k*3] - cam[0], ay = s.v[k*3+1] - cam[1], az = s.v[k*3+2] - cam[2];
            double rx = ax * cyw - az * syw;
            double rz = ax * syw + az * cyw;
            o.v[k*3]   = ox + rx * scale;
            o.v[k*3+1] = oy - (ay * cp + rz * sp) * scale;
            o.v[k*3+2] = -ay * sp + rz * cp;
        }
        memcpy(o.col, s.col, 3);
        out->push_back(o);
    }
}

// --- le monde ---------------------------------------------------------------

struct World {
    std::vector<std::vector<Tri>> pieces;
    double lo[3], hi[3];
    size_t ntri = 0;
    std::unordered_map<std::string, std::vector<std::string>> mpd;
    std::string main_name;
};

static World load_world(Lib& lib, const std::string& model) {
    std::string txt;
    World w;
    for (int j = 0; j < 3; j++) { w.lo[j] = 1e18; w.hi[j] = -1e18; }
    if (!read_file(model, &txt)) { fprintf(stderr, "%s illisible\n", model.c_str()); exit(2); }
    w.main_name = load_mpd(txt, &w.mpd);
    const std::unordered_map<std::string, std::vector<std::string>>& mpd = w.mpd;
    static const double IDENT[12] = {1,0,0, 0,1,0, 0,0,1, 0,0,0};
    std::vector<Placement> pl;
    placements(mpd, w.main_name, IDENT, 16, &pl, 0);

    w.pieces.reserve(pl.size());
    for (const Placement& p : pl) {
        double a=p.M[0],b=p.M[1],c=p.M[2],d=p.M[3],e=p.M[4],f=p.M[5],g=p.M[6],h=p.M[7],i=p.M[8];
        double tx=p.M[9],ty=p.M[10],tz=p.M[11];
        RGB base = lib.rgb(p.colour != 16 ? p.colour : 7);
        const std::vector<Tri3>& src = lib.geom(p.ref);
        std::vector<Tri> tris;
        tris.reserve(src.size());
        for (const Tri3& s : src) {
            RGB col = (s.colour == 16) ? base : lib.rgb(s.colour);
            double v[9];
            for (int k = 0; k < 3; k++) {
                double px = s.p[k*3], py = s.p[k*3+1], pz = s.p[k*3+2];
                v[k*3]   =  a * px + b * py + c * pz + tx;
                v[k*3+1] = -(d * px + e * py + f * pz + ty);
                v[k*3+2] =  g * px + h * py + i * pz + tz;
            }
            double ux = v[3]-v[0], uy = v[4]-v[1], uz = v[5]-v[2];
            double vx = v[6]-v[0], vy = v[7]-v[1], vz = v[8]-v[2];
            double nx = uy*vz - uz*vy, ny = uz*vx - ux*vz, nz = ux*vy - uy*vx;
            double nl = sqrt(nx*nx + ny*ny + nz*nz);
            if (nl < 1e-9) continue;                       // triangle degenere
            double k2 = 0.30 + 0.70 * fabs((nx*LX + ny*LY + nz*LZ) / nl);
            Tri o;
            memcpy(o.v, v, sizeof v);
            const uint8_t ch[3] = { col.r, col.g, col.b };
            for (int j = 0; j < 3; j++) {
                int q = (int)(ch[j] * k2);                 // piege 4 : troncature
                o.col[j] = (uint8_t)(q < 255 ? q : 255);
            }
            tris.push_back(o);
            for (int k = 0; k < 3; k++)
                for (int j = 0; j < 3; j++) {
                    double val = v[k*3+j];
                    if (val < w.lo[j]) w.lo[j] = val;
                    if (val > w.hi[j]) w.hi[j] = val;
                }
        }
        w.ntri += tris.size();
        w.pieces.push_back(std::move(tris));
    }
    return w;
}

// layers_zoom.frame_fit, applique a une boite englobante : extremes pris en espace
// ecran, puis centre camera resolu pour les ramener au centre de l'image
struct Fit { double cam[3]; double scale; };

static void extents(const std::array<double,6>& b, double yaw, double* o) {
    double cp = cos(PITCH), sp = sin(PITCH);
    double cyw = cos(yaw), syw = sin(yaw);
    double xlo = 1e18, xhi = -1e18, ylo = 1e18, yhi = -1e18;
    for (int u = 0; u < 2; u++) for (int v = 0; v < 2; v++) for (int w = 0; w < 2; w++) {
        double bx = u ? b[3] : b[0], by = v ? b[4] : b[1], bz = w ? b[5] : b[2];
        double sx = bx * cyw - bz * syw;
        double sy = by * cp + (bx * syw + bz * cyw) * sp;
        if (sx < xlo) xlo = sx;
        if (sx > xhi) xhi = sx;
        if (sy < ylo) ylo = sy;
        if (sy > yhi) yhi = sy;
    }
    yhi += HEAD * (yhi - ylo);                     // ciel : on voit arriver la piece
    o[0] = xlo; o[1] = xhi; o[2] = ylo; o[3] = yhi;
}

static Fit fit_box(const std::array<double,6>& b, double yaw, double fex, double fey) {
    double cp = cos(PITCH), cyw = cos(yaw), syw = sin(yaw);
    double e[4];
    extents(b, yaw, e);
    double mx = (e[0] + e[1]) * 0.5, my = (e[2] + e[3]) * 0.5;
    double ex = std::max(std::max((e[1] - e[0]) * 0.5, fex), 1e-6);
    double ey = std::max(std::max((e[3] - e[2]) * 0.5, fey), 1e-6);
    Fit f;
    f.cam[0] = mx * cyw; f.cam[1] = my / cp; f.cam[2] = -mx * syw;
    f.scale = std::min(W * MARGIN / (2 * ex), H * MARGIN / (2 * ey));
    return f;
}

// ldrawlib.group_sizes : nombre de pieces feuilles par reference de premier
// niveau. Les pieces d'un sous-modele sont contigues dans placements(), donc les
// tailles cumulees donnent les frontieres des sous-ensembles.
static std::vector<size_t> group_sizes(const std::unordered_map<std::string, std::vector<std::string>>& files,
                                       const std::string& name) {
    static const double IDENT[12] = {1,0,0, 0,1,0, 0,0,1, 0,0,0};
    std::string cur = name;
    for (;;) {
        std::vector<size_t> out;
        std::vector<std::string> refs;
        auto it = files.find(cur);
        if (it == files.end()) return out;
        for (const std::string& ln : it->second) {
            auto t = split(ln);
            if (t.size() < 15 || t[0] != "1") continue;
            int c; double m[12];
            if (!to_int(t[1], &c)) continue;
            bool ok = true;
            for (int j = 0; j < 12 && ok; j++) ok = to_double(t[2 + j], &m[j]);
            if (!ok) continue;
            std::string ref = lower(join(t, 14));
            for (char& ch : ref) if (ch == '\\') ch = '/';
            refs.push_back(ref);
            if (files.count(ref)) {
                std::vector<Placement> sub;
                placements(files, ref, IDENT, 16, &sub, 1);
                out.push_back(sub.size());
            } else out.push_back(1);
        }
        // un niveau qui ne reference qu'un seul sous-modele n'offre aucun
        // decoupage : on descend jusqu'au premier qui en propose plusieurs.
        // Le 10143 est dans ce cas -- son principal ne contient que Core.ldr.
        if (out.size() == 1 && !refs.empty() && files.count(refs[0])) { cur = refs[0]; continue; }
        return out;
    }
}

// --- le montage (walle3.py) -------------------------------------------------

struct Frame { double yaw; int cur; double t; };

struct Montage {
    Cam cam;                                   // cam.scale == tight
    double wide, R;
    std::vector<std::array<double,2>> cent;
    std::vector<std::array<double,3>> scatter;
    std::vector<Tri> ground;
    std::vector<Frame> fr;
    // boite des pieces 0..k-1 : elle ne fait que croitre, d'ou un dezoom monotone
    std::vector<std::array<double,6>> pre;
    // --zoom fit : (camera, echelle) par image, echelle en minimum courant
    std::vector<std::array<double,4>> track;
    std::vector<std::array<double,6>> bbox;      // boite monde de chaque piece
};

static Montage build_montage(const World& w) {
    Montage m;
    m.cam = make_cam(w.lo, w.hi, 0.90);
    size_t n = w.pieces.size();
    std::vector<double> miny(n);
    std::vector<std::array<double,6>>& bbox = m.bbox;
    bbox.resize(n);

    for (size_t i = 0; i < n; i++) {
        double sx = 0.0, sz = 0.0; long k = 0;
        double bl[3] = {1e18,1e18,1e18}, bh[3] = {-1e18,-1e18,-1e18};
        for (const Tri& t : w.pieces[i])
            for (int v = 0; v < 3; v++) {
                sx += t.v[v*3]; sz += t.v[v*3+2]; k++;
                for (int j = 0; j < 3; j++) {
                    if (t.v[v*3+j] < bl[j]) bl[j] = t.v[v*3+j];
                    if (t.v[v*3+j] > bh[j]) bh[j] = t.v[v*3+j];
                }
            }
        if (k) {
            m.cent.push_back({sx / k - m.cam.c[0], sz / k - m.cam.c[2]});
            miny[i] = bl[1];
            bbox[i] = {bl[0],bl[1],bl[2],bh[0],bh[1],bh[2]};
        } else {
            m.cent.push_back({0.0, 0.0});
            miny[i] = 0.0;
            bbox[i] = {0,0,0,0,0,0};
        }
    }
    m.R = 0.0;
    for (const auto& c : m.cent) m.R = std::max(m.R, hypot(c[0], c[1]));
    if (m.R == 0.0) m.R = 1.0;
    double ground = w.lo[1];

    m.pre.resize(n + 1);
    m.pre[0] = {1e18, 1e18, 1e18, -1e18, -1e18, -1e18};
    for (size_t i = 0; i < n; i++)
        for (int j = 0; j < 3; j++) {
            m.pre[i+1][j]   = std::min(m.pre[i][j],   bbox[i][j]);
            m.pre[i+1][3+j] = std::max(m.pre[i][3+j], bbox[i][3+j]);
        }

    // tas : NPILES amas, tirage deterministe -- cf. pyrandom.h
    PyRandom rng(SEED);
    std::vector<std::array<double,2>> piles(NPILES);
    for (int k = 0; k < NPILES; k++)
        piles[k] = { PILE_R * m.R * cos(2 * M_PI * k / NPILES),
                     PILE_R * m.R * sin(2 * M_PI * k / NPILES) };
    for (size_t i = 0; i < n; i++) {
        if (!NPILES) {
            // sans tas : chute verticale, aucun tirage consomme. La hauteur est
            // proportionnelle a ce qui est DEJA construit, pas au modele fini :
            // avec --zoom fit la camera est serree sur les premieres pieces.
            const std::array<double,6>& q = m.pre[std::max<size_t>(i, 1)];
            double sp2 = std::max(std::max(q[3] - q[0], q[4] - q[1]), q[5] - q[2]);
            m.scatter.push_back({0.0, DROP * sp2, 0.0});
            continue;
        }
        const std::array<double,2>& p = piles[rng.randrange(NPILES)];
        double tx = p[0] + rng.gauss(0, PILE_SIG * m.R);
        double tz = p[1] + rng.gauss(0, PILE_SIG * m.R);
        double y  = ground - miny[i] + fabs(rng.gauss(0, PILE_H * m.R));
        m.scatter.push_back({tx - m.cent[i][0], y, tz - m.cent[i][1]});
    }

    // boite elargie : modele + tas, chaque piece avec SON bbox
    double wlo[3] = {w.lo[0], w.lo[1], w.lo[2]}, whi[3] = {w.hi[0], w.hi[1], w.hi[2]};
    for (size_t i = 0; i < n; i++)
        for (int j = 0; j < 3; j++) {
            wlo[j] = std::min(wlo[j], bbox[i][j] + m.scatter[i][j]);
            whi[j] = std::max(whi[j], bbox[i][3+j] + m.scatter[i][j]);
        }
    m.wide = fit_scale(m.cam.c, wlo, whi, 0.97);

    double g = 3.4 * m.R, gy = ground - 4;
    double cx = m.cam.c[0], cz = m.cam.c[2];
    const double q[2][9] = {
        { cx-g, gy, cz-g,  cx+g, gy, cz-g,  cx+g, gy, cz+g },
        { cx-g, gy, cz-g,  cx+g, gy, cz+g,  cx-g, gy, cz+g },
    };
    for (int i = 0; i < 2; i++) {
        Tri t; memcpy(t.v, q[i], sizeof t.v);
        t.col[0] = 58; t.col[1] = 64; t.col[2] = 74;
        m.ground.push_back(t);
    }

    // schedule()
    double yaw = YAW, step = 2 * M_PI / SLOTS;
    for (size_t i = 0; i < n; i++) {
        double a = m.cent[i][0], c = m.cent[i][1];
        if (hypot(a, c) > MINR * m.R && (a * sin(yaw) + c * cos(yaw)) > BACK * m.R) {
            double tgt = pyround(atan2(-a, -c) / step) * step;          // piege 5
            double d = pymod(tgt - yaw + M_PI, 2 * M_PI) - M_PI;        // piege 6
            if (fabs(d) > 1e-3) {
                for (int f = 1; f <= ROT; f++) {
                    double t = (double)f / ROT;
                    m.fr.push_back({yaw + d * t * t * (3 - 2 * t), (int)i, -1.0});
                }
                yaw += d;
            }
        }
        for (int k = 0; k < FLY + SETTLE; k++)
            m.fr.push_back({yaw, (int)i, std::min(1.0, (double)(k + 1) / FLY)});
    }
    for (int s = 0; s < SPIN; s++)
        m.fr.push_back({yaw + 2 * M_PI * s / SPIN, (int)n, 1.0});

    // piste de zoom : l'echelle est un minimum courant, la camera ne sait que
    // reculer. Precalculee sur tout le film, donc l'image reste fonction de son
    // seul numero et tous les workers calculent la meme chose.
    if (ZOOM_FIT) {
        m.track.resize(m.fr.size());
        double best = 0.0;
        bool have = false;
        for (size_t i = 0; i < m.fr.size(); i++) {
            const Frame& f = m.fr[i];
            double e = std::max(f.t, 0.0);
            e = e * e * (3 - 2 * e);
            double ext[4];
            extents(m.pre[n], f.yaw, ext);
            double fex = ZOOM_MIN * (ext[1] - ext[0]) * 0.5, fey = ZOOM_MIN * (ext[3] - ext[2]) * 0.5;
            Fit a = fit_box(m.pre[std::max(f.cur, 1)], f.yaw, fex, fey);
            Fit b = fit_box(m.pre[std::min((size_t)f.cur + 1, n)], f.yaw, fex, fey);
            double sc = a.scale + (b.scale - a.scale) * e;
            if (!have || sc < best) { best = sc; have = true; }
            for (int j = 0; j < 3; j++) m.track[i][j] = a.cam[j] + (b.cam[j] - a.cam[j]) * e;
            m.track[i][3] = best;
        }
    }
    return m;
}

// --- le mode notice (walle3.setup_notice / frame_notice) --------------------

struct Notice {
    std::vector<std::pair<size_t,size_t>> bounds;   // tranche de pieces par groupe
    std::vector<bool> staged;                       // faux si groupe d'une seule piece
    std::vector<std::array<double,6>> gbox;         // boite du groupe
    std::vector<std::array<double,3>> goff;         // decalage vers l'atelier
    std::vector<std::array<double,6>> gpre;         // boite cumulee DANS le groupe
    struct F { int kind; int gi; int cur; double t; double yaw; };
    std::vector<F> fr;
    std::vector<std::array<double,5>> track;        // cam[3], echelle, yaw
};

static inline double smoothstep(double t) { return t * t * (3 - 2 * t); }

// derivee ET courbure nulles aux deux bouts : le smoothstep ordinaire fait
// partir le recul de camera un peu sec
static inline double smoothstep5(double t) { return t * t * t * (t * (t * 6 - 15) + 10); }

static double box_span(const std::array<double,6>& q) {
    return std::max(std::max(q[3] - q[0], q[4] - q[1]), q[5] - q[2]);
}

// la boite d'un sous-ensemble a l'atelier, ciel de chute compris
static std::array<double,6> stage_box(const std::array<double,6>& q, const std::array<double,3>& o) {
    double d = DROP * box_span(q);
    return { q[0]+o[0], q[1]+o[1], q[2]+o[2], q[3]+o[0], q[4]+o[1]+d, q[5]+o[2] };
}

// entre deux cadrages. L'echelle est GEOMETRIQUE : un zoom se percoit
// multiplicativement, une interpolation lineaire donne une vitesse inegale.
static Fit lerp_fit(const Fit& a, const Fit& b, double e) {
    Fit f;
    for (int j = 0; j < 3; j++) f.cam[j] = a.cam[j] + (b.cam[j] - a.cam[j]) * e;
    f.scale = a.scale * pow(b.scale / a.scale, e);
    return f;
}

static Notice build_notice(const World& w, const Montage& m) {
    Notice nt;
    size_t n = w.pieces.size();
    std::vector<size_t> sizes = group_sizes(w.mpd, w.main_name);
    size_t k = 0;
    for (size_t sz : sizes) { nt.bounds.push_back({k, k + sz}); k += sz; }
    double ground = m.pre[n][1];
    double sx = m.cam.c[0] + STAGE_R * m.R, sz_ = m.cam.c[2];   // l'atelier, a cote

    nt.gpre.resize(n);
    for (auto& ab : nt.bounds) {
        size_t a = ab.first, b = ab.second;
        std::array<double,6> q = {1e18,1e18,1e18,-1e18,-1e18,-1e18};
        for (size_t j = a; j < b; j++)
            for (int d = 0; d < 3; d++) {
                q[d]   = std::min(q[d],   m.bbox[j][d]);
                q[3+d] = std::max(q[3+d], m.bbox[j][3+d]);
            }
        bool st = (b - a) > 1;
        nt.staged.push_back(st);
        nt.gbox.push_back(q);
        nt.goff.push_back({ st ? sx - (q[0] + q[3]) * 0.5 : 0.0,
                            st ? ground - q[1] : 0.0,
                            st ? sz_ - (q[2] + q[5]) * 0.5 : 0.0 });
        std::array<double,6> p = {1e18,1e18,1e18,-1e18,-1e18,-1e18};
        for (size_t j = a; j < b; j++) {
            for (int d = 0; d < 3; d++) {
                p[d]   = std::min(p[d],   m.bbox[j][d]);
                p[3+d] = std::max(p[3+d], m.bbox[j][3+d]);
            }
            nt.gpre[j] = p;
        }
    }

    // rotation pour degager le point d'accroche de la piece qui arrive. Meme
    // regle que schedule() en mode tas, mais relative au SUJET DU PLAN : le
    // sous-ensemble a l'atelier, et non le modele.
    double yaw = YAW, step = 2 * M_PI / SLOTS;
    auto turn_xz = [&](double px, double pz, const std::array<double,6>& subj) -> double {
        double a_ = px - (subj[0] + subj[3]) * 0.5;
        double c_ = pz - (subj[2] + subj[5]) * 0.5;
        double rad = 0.5 * hypot(subj[3] - subj[0], subj[5] - subj[2]);
        if (rad <= 0.0) return 0.0;
        if (hypot(a_, c_) > MINR * rad && (a_ * sin(yaw) + c_ * cos(yaw)) > BACK * rad) {
            double tgt = pyround(atan2(-a_, -c_) / step) * step;
            double d = pymod(tgt - yaw + M_PI, 2 * M_PI) - M_PI;
            if (fabs(d) > 1e-3) return d;
        }
        return 0.0;
    };
    auto swing = [&](size_t gi, size_t j, double d) {
        for (int f = 1; f <= ROT; f++)
            nt.fr.push_back({0, (int)gi, (int)j, -1.0, yaw + d * smoothstep((double)f / ROT)});
    };

    for (size_t gi = 0; gi < nt.bounds.size(); gi++) {
        size_t a = nt.bounds[gi].first, b = nt.bounds[gi].second;
        auto turn_for = [&](size_t j, const std::array<double,6>& subj) {
            return turn_xz((m.bbox[j][0] + m.bbox[j][3]) * 0.5,
                           (m.bbox[j][2] + m.bbox[j][5]) * 0.5, subj);
        };
        if (!nt.staged[gi]) {
            double d = turn_for(a, m.pre[b]);
            if (d != 0.0) { swing(gi, a, d); yaw += d; }
            for (int f = 0; f < FLY + SETTLE; f++)
                nt.fr.push_back({0, (int)gi, (int)a, std::min(1.0, (double)(f + 1) / FLY), yaw});
        } else {
            for (size_t j = a; j < b; j++) {
                double d = turn_for(j, nt.gpre[j]);
                if (d != 0.0) { swing(gi, j, d); yaw += d; }
                for (int f = 0; f < FLY + SETTLE; f++)
                    nt.fr.push_back({0, (int)gi, (int)j, std::min(1.0, (double)(f + 1) / FLY), yaw});
            }
            // le bloc vole ET la camera pivote, en un seul mouvement : elle
            // recule vers le modele en se placant face au point d'accroche
            const std::array<double,6>& gq = nt.gbox[gi];
            double dt = turn_xz((gq[0] + gq[3]) * 0.5, (gq[2] + gq[5]) * 0.5, m.pre[b]);
            for (int f = 0; f < TRANSFER; f++) {
                double t = (double)(f + 1) / TRANSFER;
                nt.fr.push_back({1, (int)gi, (int)b - 1, t, yaw + dt * smoothstep5(t)});
            }
            yaw += dt;
            // une pause sur le modele : on voit ce qu'on vient de lui rattacher
            // avant de repartir a l'atelier
            for (int f = 0; f < HOLD; f++)
                nt.fr.push_back({3, (int)gi, (int)b - 1, (double)(f + 1) / HOLD, yaw});
        }
    }
    for (int f = 0; f < SPIN; f++)
        nt.fr.push_back({2, (int)nt.bounds.size(), (int)n, (double)f / SPIN,
                         yaw + 2 * M_PI * f / SPIN});

    nt.track.resize(nt.fr.size());
    for (size_t idx = 0; idx < nt.fr.size(); idx++) {
        const Notice::F& f = nt.fr[idx];
        double yaw = f.yaw;
        Fit fit;
        if (f.kind == 2) {
            fit = fit_box(m.pre[n], yaw, 0.0, 0.0);
        } else if (f.kind == 3) {
            fit = fit_box(m.pre[nt.bounds[f.gi].second], yaw, 0.0, 0.0);
        } else if (f.kind == 0 && !nt.staged[f.gi]) {
            Fit a = fit_box(m.pre[std::max<size_t>(nt.bounds[f.gi].first, 1)], yaw, 0.0, 0.0);
            Fit b = fit_box(m.pre[nt.bounds[f.gi].second], yaw, 0.0, 0.0);
            fit = lerp_fit(a, b, smoothstep5(std::max(f.t, 0.0)));
        } else if (f.kind == 0) {
            // le cadrage est interpole SUR LE VOL de la piece : sinon il saute
            // d'un cran a chaque piece posee, et le recul part sec
            size_t a0 = nt.bounds[f.gi].first;
            const std::array<double,3>& o = nt.goff[f.gi];
            const std::array<double,6>& qb = nt.gpre[f.cur];
            const std::array<double,6>& qa = (size_t)f.cur > a0 ? nt.gpre[f.cur - 1] : qb;
            Fit a = fit_box(stage_box(qa, o), yaw, 0.0, 0.0);
            Fit b = fit_box(stage_box(qb, o), yaw, 0.0, 0.0);
            fit = lerp_fit(a, b, smoothstep5(std::max(f.t, 0.0)));
        } else {
            // meme boite que la derniere image de montage, ciel de chute compris :
            // sans ca le cadrage saute d'un cran a l'entree du vol
            Fit a = fit_box(stage_box(nt.gbox[f.gi], nt.goff[f.gi]), yaw, 0.0, 0.0);
            Fit b = fit_box(m.pre[nt.bounds[f.gi].second], yaw, 0.0, 0.0);
            fit = lerp_fit(a, b, smoothstep5(f.t));
        }
        nt.track[idx] = { fit.cam[0], fit.cam[1], fit.cam[2], fit.scale, yaw };
    }
    return nt;
}

static void render_notice(const World& w, const Montage& m, const Notice& nt, size_t idx,
                          std::vector<uint8_t>& cb, std::vector<float>& zb,
                          std::vector<Tri>& scratch) {
    size_t n = w.pieces.size();
    const Notice::F& f = nt.fr[idx];
    double cam[3] = { nt.track[idx][0], nt.track[idx][1], nt.track[idx][2] };
    double scale = nt.track[idx][3], yaw = nt.track[idx][4];
    double cp = cos(PITCH), sp = sin(PITCH), cyw = cos(yaw), syw = sin(yaw);

    background(cb, W, H);
    std::fill(zb.begin(), zb.end(), FAR_Z);
    project_piece(m.ground, cam, scale, cyw, syw, cp, sp, &scratch);
    draw(cb, zb, scratch.data(), scratch.size(), W, H);          // le sol d'abord

    size_t a = f.kind == 2 ? n : nt.bounds[f.gi].first;
    size_t b = f.kind == 2 ? n : nt.bounds[f.gi].second;
    double frac = f.kind == 2 ? (double)n : f.kind == 3 ? (double)b : (double)a;
    for (size_t j = 0; j < n; j++) {
        double ox, oy, oz;
        if (f.kind == 3) {
            // pause : le modele tel qu'il est CONSTRUIT, pas le modele fini
            if (j >= b) continue;
            ox = oy = oz = 0.0;
        }
        else if (j < a) {
            // a l'atelier on ne montre QUE le sous-ensemble : le modele deja
            // assemble n'apparait qu'au moment ou le bloc part s'y poser
            if (f.kind == 0 && nt.staged[f.gi]) continue;
            ox = oy = oz = 0.0;
        }
        else if (f.kind == 2) { ox = oy = oz = 0.0; }
        else if (j >= b) continue;                               // pas encore construit
        else if (!nt.staged[f.gi]) {                             // piece seule
            double e = smoothstep(std::max(f.t, 0.0));
            ox = oz = 0.0;
            oy = DROP * box_span(nt.gpre[j]) * (1 - e);
        } else if (f.kind == 0) {
            if ((int)j > f.cur) continue;
            ox = nt.goff[f.gi][0]; oy = nt.goff[f.gi][1]; oz = nt.goff[f.gi][2];
            if ((int)j == f.cur) {
                double e = smoothstep(std::max(f.t, 0.0));
                oy += DROP * box_span(nt.gpre[j]) * (1 - e);
            }
        } else {                                                 // le bloc vole
            double e = smoothstep(f.t);
            ox = nt.goff[f.gi][0] * (1 - e);
            oy = nt.goff[f.gi][1] * (1 - e);
            oz = nt.goff[f.gi][2] * (1 - e);
        }
        project_piece(w.pieces[j], cam, scale, cyw, syw, cp, sp, &scratch);
        if (ox != 0.0 || oy != 0.0 || oz != 0.0) {
            double rx = ox * cyw - oz * syw;
            double rz = ox * syw + oz * cyw;
            draw(cb, zb, scratch.data(), scratch.size(), W, H,
                 rx * scale, -(oy * cp + rz * sp) * scale, -oy * sp + rz * cp);
        } else {
            draw(cb, zb, scratch.data(), scratch.size(), W, H);
        }
    }
    progress(cb, std::min(1.0, frac / n), W, H);
}

// walle3.frame() : l'etat a l'image idx ne depend que de idx
static void render_frame(const World& w, const Montage& m, size_t idx,
                         std::vector<uint8_t>& cb, std::vector<float>& zb,
                         std::vector<Tri>& scratch) {
    size_t n = w.pieces.size();
    double cp = cos(PITCH), sp = sin(PITCH);
    double arc = ARC * m.R;
    const Frame& f = m.fr[idx];
    double yaw = f.yaw; int cur = f.cur; double t = f.t;
    double cyw = cos(yaw), syw = sin(yaw);
    double scale;
    double cam[3] = { m.cam.c[0], m.cam.c[1], m.cam.c[2] };
    if (ZOOM_FIT) {
        for (int j = 0; j < 3; j++) cam[j] = m.track[idx][j];
        scale = m.track[idx][3];
    } else {
        double p = std::min(1.0, (cur + std::max(t, 0.0)) / n / ZOOM_END);
        scale = m.wide + (m.cam.scale - m.wide) * (p * p * (3 - 2 * p));
    }

    background(cb, W, H);
    std::fill(zb.begin(), zb.end(), FAR_Z);
    project_piece(m.ground, cam, scale, cyw, syw, cp, sp, &scratch);
    draw(cb, zb, scratch.data(), scratch.size(), W, H);          // le sol d'abord

    for (size_t j = 0; j < n; j++) {
        double ox, oy, oz;
        if ((int)j < cur) { ox = oy = oz = 0.0; }
        else {
            // sans tas, rien au sol : la piece a venir n'existe pas
            if (!NPILES && (int)j > cur) continue;
            ox = m.scatter[j][0]; oy = m.scatter[j][1]; oz = m.scatter[j][2];
            if ((int)j == cur && t >= 0.0) {
                double e = t * t * (3 - 2 * t);
                ox *= 1 - e; oy *= 1 - e; oz *= 1 - e;
                oy += arc * sin(M_PI * t);
            }
        }
        project_piece(w.pieces[j], cam, scale, cyw, syw, cp, sp, &scratch);
        if (ox != 0.0 || oy != 0.0 || oz != 0.0) {
            double rx = ox * cyw - oz * syw;
            double rz = ox * syw + oz * cyw;
            draw(cb, zb, scratch.data(), scratch.size(), W, H,
                 rx * scale, -(oy * cp + rz * sp) * scale, -oy * sp + rz * cp);
        } else {
            draw(cb, zb, scratch.data(), scratch.size(), W, H);
        }
    }
    progress(cb, std::min(1.0, (double)cur / n), W, H);
}

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: lego <model.ldr|mpd> [--lib DIR] [--yaw DEG] [--size WxH]\n"
                        "            [--margin F] [--tris out.bin] [--still]\n"
                        "            [--montage [--count | --frame N | --range A B [--step N]]]\n"
                        "            [--piles N] [--pile-r F] [--pile-sig F] [--pile-h F] [--drop F]\n"
                        "            [--zoom progress|fit] [--zoom-min F] [--zoom-head F]\n"
                        "            [--mode tas|notice] [--stage-r F] [--transfer N] [--hold N]\n"
                        "            [--fly N] [--settle N] [--rot N] [--spin N]\n");
        return 2;
    }
    std::string model = argv[1], root = "../lib/ldraw", trisout;
    double margin = 0.90;
    bool still = false, montage = false, count = false;
    long fa = -1, fb = -1, step = 1;
    for (int i = 2; i < argc; i++) {
        std::string a = argv[i];
        if      (a == "--lib"     && i + 1 < argc) root = argv[++i];
        else if (a == "--tris"    && i + 1 < argc) trisout = argv[++i];
        else if (a == "--margin"  && i + 1 < argc) margin = atof(argv[++i]);
        else if (a == "--yaw"     && i + 1 < argc) YAW = radians(atof(argv[++i]));
        else if (a == "--size"    && i + 1 < argc) sscanf(argv[++i], "%dx%d", &W, &H);
        else if (a == "--frame"   && i + 1 < argc) { fa = atol(argv[++i]); fb = fa + 1; }
        else if (a == "--range"   && i + 2 < argc) { fa = atol(argv[++i]); fb = atol(argv[++i]); }
        else if (a == "--piles"     && i + 1 < argc) NPILES = atoi(argv[++i]);
        else if (a == "--pile-r"    && i + 1 < argc) PILE_R = atof(argv[++i]);
        else if (a == "--pile-sig"  && i + 1 < argc) PILE_SIG = atof(argv[++i]);
        else if (a == "--pile-h"    && i + 1 < argc) PILE_H = atof(argv[++i]);
        else if (a == "--drop"      && i + 1 < argc) DROP = atof(argv[++i]);
        else if (a == "--zoom"      && i + 1 < argc) ZOOM_FIT = std::string(argv[++i]) == "fit";
        else if (a == "--zoom-min"  && i + 1 < argc) ZOOM_MIN = atof(argv[++i]);
        else if (a == "--zoom-head" && i + 1 < argc) HEAD = atof(argv[++i]);
        else if (a == "--mode"      && i + 1 < argc) NOTICE = std::string(argv[++i]) == "notice";
        else if (a == "--stage-r"   && i + 1 < argc) STAGE_R = atof(argv[++i]);
        else if (a == "--transfer"  && i + 1 < argc) TRANSFER = atoi(argv[++i]);
        else if (a == "--hold"      && i + 1 < argc) HOLD = atoi(argv[++i]);
        else if (a == "--fly"       && i + 1 < argc) FLY = atoi(argv[++i]);
        else if (a == "--settle"    && i + 1 < argc) SETTLE = atoi(argv[++i]);
        else if (a == "--rot"       && i + 1 < argc) ROT = atoi(argv[++i]);
        else if (a == "--spin"      && i + 1 < argc) SPIN = atoi(argv[++i]);
        else if (a == "--montage") montage = true;
        else if (a == "--step"      && i + 1 < argc) step = atol(argv[++i]);
        else if (a == "--count")   count = true;
        else if (a == "--still")   still = true;
    }
    if (!montage && trisout.empty()) still = true;

    auto t0 = std::chrono::steady_clock::now();
    Lib lib;
    struct stat st;
    if (stat((root + "/parts").c_str(), &st) || !S_ISDIR(st.st_mode)) {
        fprintf(stderr, "bibliotheque LDraw introuvable : %s/parts n'existe pas\n"
                        "  telechargement : cf. README section 1 ; sinon --lib DIR\n", root.c_str());
        return 2;
    }
    lib.index(root);
    World w = load_world(lib, model);
    double tload = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();

    if (montage) {
        Montage m = build_montage(w);
        Notice nt;
        if (NOTICE) nt = build_notice(w, m);
        size_t total = NOTICE ? nt.fr.size() : m.fr.size();
        fprintf(stderr, "%zu pieces, %zu images (%.0f s de video)%s  (%.1fs)\n",
                w.pieces.size(), total, total / 30.0,
                NOTICE ? ", notice" : "", tload);
        if (count) { printf("%zu\n", total); return 0; }
        if (fa < 0) { fa = 0; fb = (long)total; }
        if (fb > (long)total) fb = (long)total;
        std::vector<uint8_t> cb((size_t)W * H * 3);
        std::vector<float> zb((size_t)W * H);
        std::vector<Tri> scratch;
        auto t1 = std::chrono::steady_clock::now();
        // --step : n'emet qu'une image sur N. Selection de sortie pure, le
        // contenu d'une image n'en depend pas -- de quoi balayer tout un film
        // en accelere sans rendre les 39 631 images.
        for (long i = fa; i < fb; i += step) {
            if (NOTICE) render_notice(w, m, nt, (size_t)i, cb, zb, scratch);
            else        render_frame(w, m, (size_t)i, cb, zb, scratch);
            printf("P6\n%d %d\n255\n", W, H);
            fwrite(cb.data(), 1, cb.size(), stdout);
        }
        fflush(stdout);
        double s = std::chrono::duration<double>(std::chrono::steady_clock::now() - t1).count();
        long nf = (fb - fa + step - 1) / step;
        fprintf(stderr, "%ld images en %.2fs  (%.3f s/image)\n", nf, s, s / (double)nf);
        return 0;
    }

    Cam cam = make_cam(w.lo, w.hi, margin);
    fprintf(stderr, "%zu pieces, %zu triangles, echelle %.6f  (%.1fs)\n",
            w.pieces.size(), w.ntri, cam.scale, tload);

    double cyw = cos(YAW), syw = sin(YAW), cp = cos(PITCH), sp = sin(PITCH);
    FILE* fb2 = nullptr;
    if (!trisout.empty()) {
        fb2 = fopen(trisout.c_str(), "wb");
        if (!fb2) { fprintf(stderr, "%s non ecrivable\n", trisout.c_str()); return 2; }
        Head hd; memcpy(hd.magic, "LDRTRIS1", 8); hd.w = W; hd.h = H; hd.n = w.ntri;
        fwrite(&hd, sizeof hd, 1, fb2);
    }
    std::vector<uint8_t> cbuf;
    std::vector<float> zbuf;
    if (still) {
        cbuf.resize((size_t)W * H * 3);
        zbuf.assign((size_t)W * H, FAR_Z);
        background(cbuf, W, H);
    }
    auto t1 = std::chrono::steady_clock::now();
    std::vector<Tri> flat;
    for (const std::vector<Tri>& piece : w.pieces) {
        project_piece(piece, cam.c, cam.scale, cyw, syw, cp, sp, &flat);
        if (fb2) fwrite(flat.data(), sizeof(Tri), flat.size(), fb2);
        if (still) draw(cbuf, zbuf, flat.data(), flat.size(), W, H);
    }
    if (fb2) { fclose(fb2); fprintf(stderr, "%s : %dx%d, %zu triangles\n", trisout.c_str(), W, H, w.ntri); }
    if (still) {
        fprintf(stderr, "rasterisation %.1fs\n",
                std::chrono::duration<double>(std::chrono::steady_clock::now() - t1).count());
        printf("P6\n%d %d\n255\n", W, H);
        fwrite(cbuf.data(), 1, cbuf.size(), stdout);
        fflush(stdout);
    }
    return 0;
}
