# FireLine

**Values-at-risk decision layer for wildfire response (Hackbarna 2026, track 4).** When a fire starts
anywhere in Catalonia, FireLine pulls the fire perimeter and a spread forecast, ranks every care home,
school, campsite, hospital and urbanisation in its path with a time-to-impact, recommends confine or
evacuate per INFOCAT practice with the exit window shown, names the reception centre and the route,
escalates what it cannot resolve as one-line questions for the coordinator, and refreshes as the fire
moves. The design principle is *deterministic engine for the common case, agent for the edge cases,
human decides*: spread, exposure, timing, routing and the decision rule are reproducible code; the agent
only investigates assets the pipeline cannot trust its own answer for, may move them in the pessimistic
direction on its own, and must escalate any optimistic move. Every number on screen traces to a tool
call. The output is a *recommendation for the INFOCAT director*; the director del pla / alcalde orders
and CECAT sends.

## Quickstart

```sh
make setup        # uv venv + uv pip install -e ".[dev]"
make test         # pytest, no network, no LLM key
make demo         # precomputes data/scenarios/ if missing, then streamlit run fireline/app.py
make precompute   # rebuild the three demo scenarios (about 30 s, no key needed)
make fetch        # pull real Gencat registers and Open-Meteo wind into data/ (network)
```

`ANTHROPIC_API_KEY` is optional: `scripts/precompute.py` runs the agent triage with a deterministic
fake LLM when no key is used (`fireline.agent.triage(scenario, llm=None)`).

## Layout

```
fireline/
  grid.py        Grid: EPSG:25831 raster grid over a bbox; xy<->rowcol; sample(array, lon, lat)
  feeds.py       Live/cached feeds with `as_of` leak guard (Deepfire, Open-Meteo, Gencat Socrata)
  fire_state.py  FireState: perimeter(t), hotspots, FROS vector
  spread.py      ArrivalRaster: CA ensemble -> arrival_p10/p50/p90, burn_prob
  exposure.py    Asset table + tiers + deterministic edge cases + needs_review flags
  decide.py      Confine/evacuate decision rule (window-based)
  routing.py     Road graph, cut times, reception centre / medical destination, latest departure
  scenario.py    Scenario: runs the engine end-to-end; scenario_id hash; to/from JSON
  agent.py       Seven tools + triage loop (Anthropic tool use); works with a fake LLM in tests
  app.py         Streamlit UI: map, asset table, coordinator queue
  config.py      Lead times, load times, thresholds, CA parameters (dicts; shown in the UI)
fixtures/        Synthetic ignition, assets, roads, reception centres near Gavarres (no network)
scripts/         fetch_data.py (real data into data/, network); precompute.py (demo scenarios)
tests/           pytest, no network, no LLM
data/scenarios/  Precomputed demo output: <name>/scenario.json+.npz, burn_prob.png, layers.json, index.json
```

## What is real and what is synthetic in v0

- **Fixtures are synthetic.** `fixtures/` holds a synthetic ignition near la Bisbal d'Empordà
  (2026-07-03 08:00 UTC, wind from 340 deg at 8 m/s), twelve assets, a simplified road graph with real
  road names, and reception centres / medical destinations. The three demo scenarios
  (`synthetic_0800`; `synthetic_1000` two hours on with GI-660 and the Can Xic track closed; `whatif_east_1000`, the same with wind from 90 deg) are
  built from them.
- **Real Gencat registers** (equipaments, care homes, campsites, schools for Baix Empordà, Gironès and
  Selva) and Open-Meteo previous-run wind are pulled by `make fetch` into `data/` through the leak-guarded
  `feeds.py`, but are not yet wired into the scenarios. Care homes and campsites carry no coordinates in
  the registers; see `data/README.md`.
- **Deepfire** perimeters and simulations need a token (`DEEPFIRE_TOKEN`); without one the CA is the
  only spread source.
- **The cellular automaton is roughly calibrated**, not validated. With `config.CA = {p0 0.5,
  minutes_per_step 4, wind_c1 0.045, wind_c2 0.30}` on the 100 m Gavarres grid, wind from 340 deg at
  8 m/s, 30 runs: p50 head-fire (downwind) front 10.8 km after 6 h (**1.8 km/h**, target 1.5 to 2.5;
  PLAN 6.2 quotes 2.2 km/h for the Gavarres afternoon head fire under tramuntana), upwind 0.3 km
  (**0.05 km/h**) and crosswind 0.3 km (0.05 km/h) after 6 h, and **9 % of the grid** burned (p >= 0.5,
  about 7,300 ha) after 12 h. The original values (`wind_c2 0.131`) gave 1.9 km/h downwind but
  1.5 km/h crosswind and 51 % of the grid burned. The CA is not calibrated on Gavarres.

## How to extend

- **Swap the arrival source.** `Scenario.run` takes an `ArrivalRaster` explicitly. Build one from Deepfire
  hourly polygons with `spread.arrival_from_polygons`, or take the elementwise earlier arrival of two
  rasters with `spread.combine(ca, deepfire)`; everything downstream only sees the raster.
- **Add a `needs_review` code.** Add the rule in `exposure.build_asset_table` (append the code and a
  human-readable note), then teach `agent.triage` what to do for it (a tool lookup, a pessimistic
  override, or an `escalate` with a default). The UI shows the code in the asset table.
- **Add a tool.** Write a plain function in `fireline/agent.py`, add its JSON schema to `TOOLS`, and
  make the fake LLM exercise it in a test. Tools read scenario state; only `add_override` and
  `escalate` write, and `add_override` refuses optimistic moves.
- **Wire real assets.** `make fetch` writes `data/assets_in.json` in the `assets_in` row shape
  (`asset_id, name, asset_class, lon, lat, municipality`, optional `occupancy`, `shelter_viable`,
  `seasonal`, `class_ambiguous`). Pass those rows instead of `fixtures/assets.json` in
  `scripts/precompute.py::build_scenario` and geocode the unlocated care homes and campsites.

See `PLAN.md` for the full design and validation plan and `CONTRACTS.md` for the binding module
contracts.
