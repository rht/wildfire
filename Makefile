PY := .venv/bin/python
PYTEST := .venv/bin/pytest
STREAMLIT := .venv/bin/streamlit
SNAPSHOTS := fixtures/snapshots/synthetic_gavarres_0001.json fixtures/snapshots/gavarres_real_0001.json
V0_SCENARIOS := data/scenarios/index.json

.PHONY: setup test snapshots demo investigate fetch precompute demo-v0 clean-scenarios clean-db
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

# v4 MVP (readme.md): fixture + real-area snapshots, the analyst UI, one agent investigation.
snapshots:
	$(PY) scripts/make_snapshots.py

$(SNAPSHOTS):
	$(PY) scripts/make_snapshots.py

demo: $(SNAPSHOTS)
	$(STREAMLIT) run fireline/app.py

investigate:
	$(PY) scripts/investigate.py

fetch:
	$(PY) scripts/fetch_data.py registers
	$(PY) scripts/fetch_data.py wind

# v0 engine (spread CA, routing, decisions): labelled enrichment behind config.FEATURES, off by default.
precompute:
	$(PY) scripts/precompute.py

$(V0_SCENARIOS):
	$(PY) scripts/precompute.py

demo-v0: $(V0_SCENARIOS)
	@echo "v0 scenarios are precomputed under data/scenarios/; the v4 UI (make demo) reads snapshots."

clean-scenarios:
	rm -rf data/scenarios

clean-db:
	rm -f data/fireline.sqlite
