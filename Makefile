PYTHON ?= python3
VENV   := .venv
BIN    := $(VENV)/bin
PY     := $(BIN)/python
ROWS   ?= 10000
SEED   ?= 42

.PHONY: setup generate clean

setup: $(VENV)/.installed

$(VENV)/.installed: requirements.txt
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -r requirements.txt
	touch $@

generate: setup
	$(PY) src/generate.py --rows $(ROWS) --seed $(SEED) --out data/bronze

clean:
	rm -rf data dbt/target dbt/logs logs .pytest_cache
