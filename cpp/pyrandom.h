// Le random de CPython, au bit pres.
//
// `walle3.geometry()` tire la position des tas avec random.Random(SEED) :
// randrange() puis gauss(). Pour que le portage pose les memes pieces aux memes
// endroits, il ne suffit pas d'un Mersenne Twister -- il faut CELUI de CPython,
// avec sa graine (init_by_array), son random() 53 bits, son rejet dans
// _randbelow(), et le cache d'une valeur sur deux de gauss().
//
// Verifie contre Python : cf. la cible `check-random` du Makefile.
#pragma once
#include <cstdint>
#include <cmath>
#include <cstddef>

struct PyRandom {
    static const int N = 624, M = 397;
    uint32_t mt[N];
    int mti;
    bool has_gauss = false;
    double gauss_next = 0.0;

    explicit PyRandom(uint32_t seed) { init_by_array(&seed, 1); }

    void init_genrand(uint32_t s) {
        mt[0] = s;
        for (mti = 1; mti < N; mti++)
            mt[mti] = (uint32_t)(1812433253U * (mt[mti-1] ^ (mt[mti-1] >> 30)) + (uint32_t)mti);
    }

    // random.seed(int) passe par la : la graine est decoupee en mots de 32 bits
    void init_by_array(const uint32_t* key, size_t klen) {
        init_genrand(19650218U);
        size_t i = 1, j = 0;
        size_t k = (N > klen ? (size_t)N : klen);
        for (; k; k--) {
            mt[i] = (mt[i] ^ ((mt[i-1] ^ (mt[i-1] >> 30)) * 1664525U)) + key[j] + (uint32_t)j;
            i++; j++;
            if (i >= (size_t)N) { mt[0] = mt[N-1]; i = 1; }
            if (j >= klen) j = 0;
        }
        for (k = N - 1; k; k--) {
            mt[i] = (mt[i] ^ ((mt[i-1] ^ (mt[i-1] >> 30)) * 1566083941U)) - (uint32_t)i;
            i++;
            if (i >= (size_t)N) { mt[0] = mt[N-1]; i = 1; }
        }
        mt[0] = 0x80000000U;
    }

    uint32_t genrand_uint32() {
        uint32_t y;
        static const uint32_t mag01[2] = { 0x0U, 0x9908b0dfU };
        if (mti >= N) {
            int kk;
            for (kk = 0; kk < N - M; kk++) {
                y = (mt[kk] & 0x80000000U) | (mt[kk+1] & 0x7fffffffU);
                mt[kk] = mt[kk+M] ^ (y >> 1) ^ mag01[y & 0x1U];
            }
            for (; kk < N - 1; kk++) {
                y = (mt[kk] & 0x80000000U) | (mt[kk+1] & 0x7fffffffU);
                mt[kk] = mt[kk+(M-N)] ^ (y >> 1) ^ mag01[y & 0x1U];
            }
            y = (mt[N-1] & 0x80000000U) | (mt[0] & 0x7fffffffU);
            mt[N-1] = mt[M-1] ^ (y >> 1) ^ mag01[y & 0x1U];
            mti = 0;
        }
        y = mt[mti++];
        y ^= (y >> 11);
        y ^= (y << 7)  & 0x9d2c5680U;
        y ^= (y << 15) & 0xefc60000U;
        y ^= (y >> 18);
        return y;
    }

    // random() : 53 bits, exactement genrand_res53
    double random() {
        uint32_t a = genrand_uint32() >> 5, b = genrand_uint32() >> 6;
        return (a * 67108864.0 + b) * (1.0 / 9007199254740992.0);
    }

    uint32_t getrandbits(int k) {          // k <= 32, le seul cas utilise ici
        if (k == 0) return 0;
        return genrand_uint32() >> (32 - k);
    }

    // _randbelow_with_getrandbits : tirage par rejet, pas un modulo
    uint32_t randrange(uint32_t n) {
        if (!n) return 0;
        int k = 0;
        for (uint32_t v = n; v; v >>= 1) k++;          // n.bit_length()
        uint32_t r = getrandbits(k);
        while (r >= n) r = getrandbits(k);
        return r;
    }

    // une valeur sur deux sort du cache : l'etat du generateur en depend
    double gauss(double mu, double sigma) {
        double z;
        if (has_gauss) { z = gauss_next; has_gauss = false; }
        else {
            double x2pi = random() * (2.0 * M_PI);
            double g2rad = sqrt(-2.0 * log(1.0 - random()));
            z = cos(x2pi) * g2rad;
            gauss_next = sin(x2pi) * g2rad;
            has_gauss = true;
        }
        return mu + z * sigma;
    }
};
