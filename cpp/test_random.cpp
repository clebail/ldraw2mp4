// Compare le random du portage a celui de CPython : cf. `make check-random`.
#include <cstdio>
#include "pyrandom.h"
int main() {
    PyRandom r(20213);
    for (int i = 0; i < 40; i++) printf("randrange7 %u\n", r.randrange(7));
    for (int i = 0; i < 40; i++) printf("random %.17g\n", r.random());
    for (int i = 0; i < 40; i++) printf("gauss %.17g\n", r.gauss(0.0, 1.5));
    // l'entrelacement reel de geometry() : randrange puis trois gauss
    // Tirages sequences un par un : l'ordre d'evaluation des arguments d'une
    // fonction n'est pas specifie en C++ (gcc les evalue de droite a gauche).
    for (int i = 0; i < 40; i++) {
        unsigned k = r.randrange(7);
        double a = r.gauss(0, 0.155);
        double b = r.gauss(0, 0.155);
        double c = fabs(r.gauss(0, 0.20));
        printf("mix %u %.17g %.17g %.17g\n", k, a, b, c);
    }
    return 0;
}
