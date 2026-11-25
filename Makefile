PYTHON ?= python3
VENV   := .venv
BIN    := $(VENV)/bin
PY     := $(BIN)/python
ROWS   ?= 10000
SEED   ?= 42
DBT    := cd dbt && DBT_VERSION_CHECK=false ../$(BIN)/dbt
DBT_ARGS := --profiles-dir .

.PHONY: setup generate run clean

setup: $(VENV)/.installed

$(VENV)/.installed: requirements.txt
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -r requirements.txt
	touch $@

generate: setup
	$(PY) src/generate.py --rows $(ROWS) --seed $(SEED) --out data/bronze

run: setup
	$(DBT) run $(DBT_ARGS)

clean:
	rm -rf data dbt/target dbt/logs logs .pytest_cache
