PY := .venv/bin/python
PYTEST := .venv/bin/pytest
STREAMLIT := .venv/bin/streamlit
SNAPSHOTS := fixtures/snapshots/synthetic_gavarres_0001.json fixtures/snapshots/gavarres_real_0001.json
V0_SCENARIOS := data/scenarios/index.json
GAVARRES := fixtures/incidents/gavarres_real
GAVARRES_DATA := data/gavarres-real
PORT ?= 18522

.PHONY: setup test snapshots demo investigate fetch precompute demo-v0 clean-scenarios clean-db
.PHONY: demo-v2 gavarres-triggers frontend-build clean-demo-v2
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

# v2 demo (readme.md "Real Gavarres perimeter in the React dashboard"): the React frontend on the incident
# runtime in recorded mode, fed the real Deepfire satellite perimeters. No telephony, no LLM, no live fetch.
# Ctrl-C stops the server; the data dir resumes on rerun (make clean-demo-v2 restarts the incident).
frontend-build: frontend/dist/index.html

frontend/dist/index.html: $(wildcard frontend/src/*.jsx frontend/src/*/*.jsx frontend/src/*/*.js frontend/src/*/*.mjs) frontend/package.json
	test -d frontend/node_modules || npm --prefix frontend ci
	npm --prefix frontend run build

gavarres-triggers:
	$(PY) scripts/make_gavarres_trigger.py

demo-v2: frontend-build
	@set -e; \
	export FIRE_TRIGGER_TOKEN=$${FIRE_TRIGGER_TOKEN:-$$(openssl rand -hex 24)}; \
	export VOICE_RESULT_TOKEN=$${VOICE_RESULT_TOKEN:-$$(openssl rand -hex 24)}; \
	$(PY) -m fireline.incident_server serve --settings $(GAVARRES)/settings.json \
	  --data-dir $(GAVARRES_DATA) --port $(PORT) & server=$$!; \
	trap 'kill $$server 2>/dev/null' EXIT INT TERM; \
	for i in $$(seq 1 50); do curl -fs http://127.0.0.1:$(PORT)/health >/dev/null && break; sleep 0.2; done; \
	for n in 0001 0002 0003; do \
	  $(PY) -m fireline.incident_server trigger --url http://127.0.0.1:$(PORT) --file $(GAVARRES)/trigger-$$n.json >/dev/null; \
	done; \
	echo "demo-v2: real Gavarres fire (recorded) at http://127.0.0.1:$(PORT)/  (Connected backend; Ctrl-C to stop)"; \
	wait $$server

clean-demo-v2:
	rm -rf $(GAVARRES_DATA)

clean-scenarios:
	rm -rf data/scenarios

clean-db:
	rm -f data/fireline.sqlite
