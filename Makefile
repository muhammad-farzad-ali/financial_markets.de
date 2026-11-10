# Convenience targets for the German markets pipeline.
#
# Usage:
#   make all
#   make collect ARGS="--only DAX,SAP.DE"
#   make refresh
#   make analyze
#   make report
#   make fundamentals
#   make fundamentals-refresh
#   make fundamentals-analyze
#   make fundamentals-report
#   make web
#   make clean

PYTHON ?= python3
ARGS ?=
FUNDAMENTALS_ARGS ?=

.PHONY: all collect refresh analyze report fundamentals fundamentals-refresh fundamentals-analyze fundamentals-report web clean

all:
	$(PYTHON) scripts/run_all.py $(ARGS)

collect:
	$(PYTHON) scripts/collect.py $(ARGS)

refresh:
	$(PYTHON) scripts/collect.py --refresh $(ARGS)

analyze:
	$(PYTHON) scripts/analyze.py

report:
	$(PYTHON) scripts/report.py

fundamentals:
	$(PYTHON) scripts/fundamentals.py all $(FUNDAMENTALS_ARGS)

fundamentals-refresh:
	$(PYTHON) scripts/fundamentals.py all --refresh $(FUNDAMENTALS_ARGS)

fundamentals-analyze:
	$(PYTHON) scripts/fundamentals.py analyze

fundamentals-report:
	$(PYTHON) scripts/fundamentals.py report

web:
	$(PYTHON) scripts/web.py

clean:
	rm -rf data/raw reports/charts
	@echo "Removed data/raw and reports/charts. Run 'make all' to rebuild."
