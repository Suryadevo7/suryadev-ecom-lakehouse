# ecom-lakehouse: local medallion ELT (bronze Parquet -> dbt-duckdb silver/gold)
#
#   make all            setup + lint + generate + run + test (what CI runs)
#   make all ROWS=500   smaller dataset
#   make clean          remove generated data and dbt artefacts (keeps .venv)
#   make distclean      also remove the virtualenv

VENV ?= .venv
ifeq ($(OS),Windows_NT)            # Git Bash on Windows: venv uses Scripts/ and python.exe
PYTHON ?= python
BIN    := $(VENV)/Scripts
else
PYTHON ?= python3
BIN    := $(VENV)/bin
endif
PY     := $(BIN)/python

ROWS   ?= 10000
SEED   ?= 42
DBT    := cd dbt && DBT_VERSION_CHECK=false ../$(BIN)/dbt
DBT_ARGS := --profiles-dir .

.DEFAULT_GOAL := help
.PHONY: help setup lint generate run test docs all clean distclean

help:
	@echo "targets: setup lint generate run test docs all clean distclean   (vars: ROWS=$(ROWS) SEED=$(SEED))"

setup: $(VENV)/.installed

$(VENV)/.installed: requirements.txt
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -r requirements.txt
	touch $@

## static checks: ruff lint + formatting
lint: setup
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

## bronze: deterministic synthetic data -> data/bronze/<entity>/ingest_date=YYYY-MM-DD/
generate: setup
	$(PY) src/generate.py --rows $(ROWS) --seed $(SEED) --out data/bronze

## silver + gold: build all dbt models into data/lakehouse.duckdb
run: setup
	$(DBT) run $(DBT_ARGS)

## dbt data tests + pytest (generator unit tests, bronze = silver + rejected reconciliation)
test: setup
	$(DBT) test $(DBT_ARGS)
	RECONCILE_STRICT=1 $(PY) -m pytest -v

## dbt docs + lineage graph as one self-contained HTML file (needs `make run` first)
docs: setup
	$(DBT) docs generate --static $(DBT_ARGS)
	@echo "open dbt/target/static_index.html in a browser"

all: setup lint generate run test

clean:
	rm -rf data dbt/target dbt/logs dbt/.user.yml logs .pytest_cache

distclean: clean
	rm -rf $(VENV)
