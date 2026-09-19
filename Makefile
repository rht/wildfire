PY := .venv/bin/python
PYTEST := .venv/bin/pytest
STREAMLIT := .venv/bin/streamlit
SCENARIOS := data/scenarios/index.json

.PHONY: setup test precompute demo fetch clean-scenarios
.PHONY: static-priorities priority-report

static-priorities:
	$(PY) scripts/static_priorities.py --all-cases --output reports/static-priority-results.json

priority-report: static-priorities
	$(PY) scripts/build_priority_report.py

setup:
	uv venv
	uv pip install -e ".[dev]"

test:
	$(PYTEST) -q

precompute:
	$(PY) scripts/precompute.py

$(SCENARIOS):
	$(PY) scripts/precompute.py

demo: $(SCENARIOS)
	$(STREAMLIT) run fireline/app.py

fetch:
	$(PY) scripts/fetch_data.py registers
	$(PY) scripts/fetch_data.py wind

clean-scenarios:
	rm -rf data/scenarios
