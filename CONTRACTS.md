# FireLine contracts (v0, minimal working version)

Everything below is what the modules agree on. See PLAN.md for the full design; this file is the
subset that v0 implements. Extend, do not break.

## Package layout

```
fireline/
  grid.py        Grid: EPSG:25831 raster grid over a bbox; xy<->rowcol; sample(array, lon, lat)
  feeds.py       Live/cached feeds with `as_of` leak guard (Deepfire, Open-Meteo, Gencat Socrata)
  fire_state.py  FireState: perimeter(t), hotspots, FROS vector
  spread.py      ArrivalRaster: CA ensemble -> arrival_p10/p50/p90, burn_prob
  exposure.py    Asset table + tiers + deterministic edge cases + needs_review flags
  decide.py      Confine/evacuate decision rule (window-based)
  routing.py     Road graph, cut times, reception centre / medical destination, latest departure
  scenario.py    Scenario: runs the engine end-to-end from a FireState; scenario_id hash; to/from JSON
  agent.py       Seven tools + triage loop (Anthropic tool use); works with a fake LLM in tests
  app.py         Streamlit UI: map, asset table, coordinator queue
  config.py      Lead times, load times, thresholds (dicts; shown in UI)
fixtures/        Synthetic ignition, assets, roads, reception centres near Gavarres (no network)
scripts/         Fetch real data into data/ (network); precompute scenarios
tests/           pytest, no network, no LLM
```

## Coordinates

- Storage CRS: EPSG:25831 (UTM 31N), metres. Input/output to users: lon/lat EPSG:4326.
- `Grid(bbox_25831=(xmin, ymin, xmax, ymax), cell=100)`; arrays are `(nrows, ncols)` numpy, row 0 = north.
- `grid.to_rowcol(x, y)`, `grid.to_xy(row, col)` (cell centre), `grid.sample(arr, lon, lat)` (nearest cell, NaN outside).
- Gavarres bbox (EPSG:4326): lon 2.85..3.20, lat 41.80..42.05. `Grid.gavarres(cell=100)`.

## Times

- All timestamps are timezone-aware UTC `datetime`. Replay times are in UTC (Gavarres local = UTC+2).
- Every feed function takes `as_of: datetime` and must never return a record newer than `as_of`
  (`feeds.LeakError` if it would).

## FireState (fire_state.py)

```python
@dataclass
class FireState:
    cluster_id: str
    t: datetime
    perimeter: shapely.Polygon | MultiPolygon   # EPSG:25831
    hotspots: list[dict]                        # {lon, lat, t: datetime, frp: float, source: str}
    fros_dir_deg: float | None                  # direction of spread, degrees clockwise from north
    fros_speed_mps: float | None
    wind_dir_deg: float                         # direction wind blows FROM, meteorological
    wind_speed_mps: float
```

## ArrivalRaster (spread.py)

```python
@dataclass
class ArrivalRaster:
    grid: Grid
    arrival_p10: np.ndarray   # minutes after t; np.inf where never burned in >=10% of runs
    arrival_p50: np.ndarray
    arrival_p90: np.ndarray
    burn_prob: np.ndarray     # 0..1
    source: str               # "ca" | "deepfire" | "min(ca,deepfire)"
    horizon_min: int
```

`spread.run_ca(fire_state, grid, fuel=None, slope=None, n_runs=50, horizon_min=720, seed=0) -> ArrivalRaster`.
`fuel`/`slope` are optional arrays on the grid (1.0 everywhere if None).

## Asset table (exposure.py)

One row per asset, a plain dict (list[dict]; pandas only in the UI):

```python
{
  "asset_id": str,             # stable: f"{source}:{source_id}"
  "name": str,
  "asset_class": str,          # "care_home"|"hospital"|"school"|"camp"|"campsite"|"nucleus"|"masia"
  "lon": float, "lat": float,
  "municipality": str,
  "occupancy": int | None,
  "occupancy_source": str,     # "register"|"allocated"|"override"|"unknown"
  "burn_prob": float,
  "arrival_p10_min": float,    # inf if never
  "arrival_p50_min": float,
  "lead_time_min": int,        # from config.LEAD_TIME_MIN[asset_class]
  "lead_adjusted_p10_min": float,
  "tier": str,                 # "act_now"|"prepare"|"monitor"
  "needs_review": list[str],   # reason codes: occupancy_unknown|occupancy_seasonal|class_ambiguous|no_exit
  "notes": list[str],          # deterministic edge-case notes, human readable
  "overrides": list[dict],     # {field, value, source, quoted_snippet, fetched_at, confidence, direction}
}
```

`exposure.build_asset_table(assets_in: list[dict], arrival: ArrivalRaster, cfg=config) -> list[dict]`
where `assets_in` rows have at least `asset_id, name, asset_class, lon, lat, municipality` and optional
`occupancy`, `occupancy_source`, `shelter_viable: bool`, `seasonal: bool`, `class_ambiguous: bool`.

Tier rule (config): act_now if lead_adjusted_p10 <= ACT_NOW_MIN and burn_prob >= BURN_PROB_MIN;
prepare if lead_adjusted_p10 <= PREPARE_MIN and burn_prob >= BURN_PROB_MIN; else monitor.

## Decision (decide.py)

```python
{
  "asset_id": str,
  "decision": "evacuate"|"confine"|"confine_request_protection"|"monitor",
  "staged": str | None,            # e.g. "confine now, evacuate when bus and route confirmed"
  "exit_window_min": float | None, # cut_time - now - load - travel - buffer; None if no route
  "latest_departure_min": float | None,
  "checks": {"exposed": {"ok": bool, "evidence": str},
             "window_open": {"ok": bool, "evidence": str},
             "shelter_viable": {"ok": bool, "evidence": str}},
  "reception_centre": str | None,  # name
  "medical_destination": str | None,
  "route": dict | None,            # from routing.py
  "issuer_note": "recommendation for the INFOCAT director; director del pla / alcalde orders, CECAT sends"
}
```

`decide.decide(asset: dict, route: dict | None, cfg=config) -> dict`.

## Routing (routing.py)

```python
route = {
  "asset_id": str,
  "destination_id": str, "destination_name": str, "destination_kind": "reception"|"medical",
  "travel_min": float,
  "first_cut_road": str | None,   # edge name
  "first_cut_min": float,         # minutes until first edge on route is cut (inf if never)
  "path_lonlat": list[tuple[float,float]],
}
```

`routing.RoadGraph.from_fixture(path)` (JSON: nodes {id: [lon, lat]}, edges [{u, v, name, highway, length_m, speed_kmh}]),
`.from_osmnx(bbox)` optional, `.apply_closures(closed_edge_names)`, `.cut_times(arrival: ArrivalRaster)`,
`routing.best_route(graph, asset, destinations: list[dict], arrival, departure_min, cfg) -> route | None`.
Destinations have `asset_id, name, kind ("reception"|"medical"), lon, lat, capacity`.
A destination is valid only if its burn_prob at departure horizon is < cfg.DEST_BURN_PROB_MAX.
`no_exit` review flag when best_route returns None.

## Scenario (scenario.py)

```python
Scenario.run(fire_state, assets_in, destinations, road_graph, cfg) -> Scenario
scenario.id           # sha1 of (cluster_id, t, config hash)[:12]
scenario.assets       # asset table (list[dict]) after decisions attached: each row also has "decision" (dict) and "route"
scenario.queue        # list of escalations: {escalation_id, asset_id, question, options, default, status, answer}
scenario.change_log   # list[str]
scenario.to_json(path) / Scenario.from_json(path)
```

## Agent tools (agent.py)

Exactly these seven in v0, each a plain Python function with a JSON-schema entry in `TOOLS`:

- `get_assets(scenario_id, tier=None, needs_review=None) -> list[dict]` (trimmed rows)
- `get_decision(scenario_id, asset_id) -> dict`
- `get_route(scenario_id, asset_id) -> dict | None`
- `lookup_facility(query: str) -> list[dict]` across fixture/real registers (name substring match)
- `sample_raster(scenario_id, layer: "arrival_p10"|"arrival_p50"|"burn_prob", lon, lat) -> float`
- `add_override(scenario_id, asset_id, field, value, source, quoted_snippet, confidence) -> dict`
  Only pessimistic direction allowed (occupancy up, tier up, shelter_viable False); else raises `OptimisticMoveError`.
- `escalate(scenario_id, asset_id, question, options: list[str], default: str) -> dict` (appends to queue, applies default)

`agent.triage(scenario, llm=None, max_steps_per_asset=6)`: for each asset with needs_review, in tier order,
run an Anthropic tool-use loop. `llm=None` means use `FakeLLM` (deterministic: escalate with the pessimistic default)
so tests and offline demo run without a key. Model: `claude-sonnet-5` (env `FIRELINE_MODEL` overrides).
Post-check: every integer/float in the assistant's final text must appear in some tool result of that turn.

## Config (config.py)

```python
LEAD_TIME_MIN = {"care_home": 180, "hospital": 180, "camp": 120, "school": 120, "campsite": 90, "nucleus": 60, "masia": 60}
LOAD_TIME_MIN = {"care_home": 90, "hospital": 90, "camp": 30, "school": 30, "campsite": 20, "nucleus": 45, "masia": 15}
ACT_NOW_MIN = 120; PREPARE_MIN = 360; BURN_PROB_MIN = 0.2; BURN_PROB_HIGH = 0.7
ROUTE_BUFFER_MIN = 20; DEST_BURN_PROB_MAX = 0.1; DEST_MARGIN_MIN = 60
CA = {"p0": 0.58, "minutes_per_step": 4, "wind_c1": 0.045, "wind_c2": 0.131, "slope_a": 0.078}
```
