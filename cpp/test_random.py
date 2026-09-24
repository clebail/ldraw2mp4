#!/usr/bin/env python3
"""Reference du random pour le portage : meme sequence que test_random.cpp."""
import random, math
r = random.Random(20213)
for _ in range(40): print('randrange7 %d' % r.randrange(7))
for _ in range(40): print('random %.17g' % r.random())
for _ in range(40): print('gauss %.17g' % r.gauss(0.0, 1.5))
for _ in range(40):
    print('mix %d %.17g %.17g %.17g' % (r.randrange(7), r.gauss(0, 0.155),
                                        r.gauss(0, 0.155), abs(r.gauss(0, 0.20))))
