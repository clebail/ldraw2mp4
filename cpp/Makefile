# Compilation et validation du portage C++.  `make help` pour la liste.
#
# La validation compare le binaire au rendu Python, au pixel pres.
#
# Toutes les cibles travaillent sur un couple (NAME, MODEL). WALL-E par defaut,
# le Faucon via le prefixe `falcon-` : `make falcon-check-raster`.

PY       ?= python3
CXX      ?= c++
ARCH     := $(shell uname -m)
ifeq ($(ARCH),arm64)
TUNE     ?= -mcpu=native
else
TUNE     ?= -march=native
endif
# -ffp-contract=off : sans lui le compilateur fusionne `a*b + c*d` en FMA et
# change le dernier bit. L'image y survit, le dump de triangles non.
CXXFLAGS ?= -O2 -std=c++17 -Wall $(TUNE) -ffp-contract=off

BUILD    := build
REF      := ref
NAME     ?= walle
MODEL    ?= ../models/omr/21303-walle.mpd
FALCON   := ../models/omr/10179-millennium-falcon.mpd
CMP      := $(PY) ../src/cmp_ppm.py
GEN      := $(PY) ref_still.py

STILL    := $(REF)/$(NAME)_still.ppm.gz
TRIS     := $(REF)/$(NAME)_tris.bin
MPREFIX  := $(REF)/$(NAME)_m
# images de montage temoins : debut, premier vol, une rotation, le zoom en
# cours, la fin du montage, le tour final
MFRAMES  ?= 0 3 137 1573 3143 4714 6200 6285
# un reglage NON par defaut, pour que les options soient validees elles aussi
OPTSET   ?= --piles 0 --zoom fit
OPTFRAMES?= 0 400 3000
# rotation de degagement, atelier, vol du bloc (avec rotation), pause, tour final
NOTFRAMES?= 1201 1209 1228 1246 4781 7318
FALCON_MFRAMES := 0 3 20000 39571

# etape 1 : lit un tris.bin, ecrit un PPM sur stdout
RASTER   ?= $(BUILD)/raster_ppm
# etape 2 : lit un .ldr/.mpd, ecrit un PPM sur stdout -- le portage complet
CAND     ?= $(BUILD)/lego

DEPS     := ref_still.py ../src/engine.py ../src/ldrawlib.py

.PHONY: help port ref tris ref-montage verify check check-raster check-tris check-montage check-opts check-notice check-random clean

help:
	@echo "port           compile le portage : $(RASTER) et $(CAND)"
	@echo "ref            (re)genere la reference Python  -> $(STILL)"
	@echo "tris           dump des triangles projetes     -> $(TRIS)   [non versionne]"
	@echo "verify         regenere la reference et la compare a celle du depot"
	@echo "               -> a lancer en premier sur toute nouvelle machine : les"
	@echo "                  sin/cos passent par la libm, rien ne garantit le meme bit"
	@echo "check-tris     le dump du portage contre celui de Python, octet a octet"
	@echo "check-raster   RASTER=$(RASTER) contre la reference (rastériseur seul)"
	@echo "check          CAND=$(CAND) contre la reference (chaine complete)"
	@echo "check-montage  des images du film contre la reference walle3"
	@echo "check-opts     les options de tas et de cadrage, sur un reglage non par defaut"
	@echo "check-notice   le montage en notice : atelier, vol du bloc, pose"
	@echo "check-random   le Mersenne Twister du portage contre celui de CPython"
	@echo "clean          supprime $(BUILD)/ et les dumps"
	@echo ""
	@echo "prefixe falcon-  meme cible sur le Faucon : make falcon-check-raster"
	@echo "NAME=$(NAME)  MODEL=$(MODEL)"

# --- compilation ------------------------------------------------------------

port: $(RASTER) $(CAND)

$(BUILD)/%: %.cpp | $(BUILD)
	$(CXX) $(CXXFLAGS) -o $@ $<

$(BUILD):
	mkdir -p $@

# --- references Python ------------------------------------------------------

ref: $(STILL)

$(STILL): $(DEPS)
	@mkdir -p $(REF)
	$(GEN) $(MODEL) --ppm $@

tris: $(TRIS)

ref-montage:
	@mkdir -p $(REF)
	$(PY) ref_montage.py $(MODEL) --frames $(shell echo $(MFRAMES) | tr ' ' ,) --prefix $(MPREFIX)

$(TRIS): $(DEPS)
	@mkdir -p $(REF)
	$(GEN) $(MODEL) --tris $@ --ppm $(STILL)

# --- validation -------------------------------------------------------------

# meme machine, meme code : doit etre identique. Sur une AUTRE machine, un ecart
# ici ne prouve pas un bug du portage, il dit que la reference doit etre
# regeneree localement (libm differente).
verify: $(STILL) | $(BUILD)
	$(GEN) $(MODEL) --ppm $(BUILD)/verify_$(NAME).ppm.gz
	$(CMP) $(STILL) $(BUILD)/verify_$(NAME).ppm.gz

# etape 1 : les memes triangles des deux cotes. Aucun parseur en jeu, donc un
# ecart ne peut venir que du rastériseur.
check-raster: $(TRIS) $(RASTER)
	./$(RASTER) $(TRIS) | $(CMP) $(STILL) - --diff $(BUILD)/diff_$(NAME).ppm

# le dump avant l'image : un parseur faux echoue ici a un offset precis, alors
# qu'il donnerait une image plausible plus loin
check-tris: $(TRIS) $(CAND)
	./$(CAND) $(MODEL) --tris $(BUILD)/$(NAME)_cpp.bin
	cmp $(TRIS) $(BUILD)/$(NAME)_cpp.bin && echo "dumps identiques"

# le tirage des tas passe par le Mersenne Twister de CPython : s'il diverge,
# les pieces ne sont pas au meme endroit et toutes les images sont fausses
check-random: $(BUILD)/test_random
	./$(BUILD)/test_random > $(BUILD)/r_cpp.txt
	$(PY) test_random.py > $(BUILD)/r_py.txt
	diff $(BUILD)/r_py.txt $(BUILD)/r_cpp.txt && echo "random identique"

# une image de montage depend du planning, du tirage des tas, du zoom, de l'arc
# de vol et du tampon de progression : chacun peut etre faux tout seul
check-montage: $(CAND)
	@for f in $(MFRAMES); do \
	  printf 'image %-6s ' $$f; \
	  ./$(CAND) $(MODEL) --montage --frame $$f 2>/dev/null | $(CMP) $(MPREFIX)$$f.ppm.gz - || exit 1; \
	done

# etape 2 : la chaine complete, parseur compris.
# les defauts sont couverts par check-montage ; ici on verifie que les OPTIONS
# elles-memes sont cablees pareil des deux cotes -- changer le nombre de tas
# change jusqu'au rejet du tirage aleatoire
check-opts: $(CAND)
	@mkdir -p $(BUILD)
	$(PY) ref_montage.py $(MODEL) $(OPTSET) --frames $(shell echo $(OPTFRAMES) | tr ' ' ,) --prefix $(BUILD)/opt_$(NAME)_
	@for f in $(OPTFRAMES); do \
	  printf 'image %-6s ' $$f; \
	  ./$(CAND) $(MODEL) --montage $(OPTSET) --frame $$f 2>/dev/null | $(CMP) $(BUILD)/opt_$(NAME)_$$f.ppm.gz - || exit 1; \
	done

# le mode notice : sous-ensembles montes a l'ecart puis poses d'un bloc
check-notice: $(CAND)
	@mkdir -p $(BUILD)
	$(PY) ref_montage.py $(MODEL) --mode notice --frames $(shell echo $(NOTFRAMES) | tr ' ' ,) --prefix $(BUILD)/not_$(NAME)_
	@for f in $(NOTFRAMES); do \
	  printf 'image %-6s ' $$f; \
	  ./$(CAND) $(MODEL) --montage --mode notice --frame $$f 2>/dev/null | $(CMP) $(BUILD)/not_$(NAME)_$$f.ppm.gz - || exit 1; \
	done

check: $(STILL) $(CAND)
	@test -x $(CAND) || { echo "$(CAND) absent : le binaire doit lire un .ldr/.mpd et ecrire un PPM sur stdout"; exit 1; }
	./$(CAND) $(MODEL) --still --lib ../lib/ldraw | $(CMP) $(STILL) - --diff $(BUILD)/diff_$(NAME).ppm

# --- raccourci Faucon -------------------------------------------------------

falcon-%:
	$(MAKE) NAME=falcon MODEL=$(FALCON) MFRAMES="$(FALCON_MFRAMES)" $*

clean:
	rm -rf $(BUILD) $(REF)/*.bin
