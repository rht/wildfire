# ResponsAra contracts (v1.1, readme.md v4 MVP)

Binding module interfaces for the v4 MVP in `readme.md`. Section 5 of the readme defines the shared
location-assessment snapshot; this file restates it as code contracts and adds the module APIs on
both sides of it. `readme.md` owns scope; this file owns shapes and signatures. Extend, do not break.
The v0 engine (spread, routing, decisions, tiers) is kept behind feature flags (section 8) and its
old contract is preserved at the end for reference.

## Package layout

```
fireline/
  config.py      Policies and flags: VALUE_POLICY, VALUE_AT_RISK_POLICY, CRITICALITY_POLICY, CUSTOM_VALUATION_POLICY,
                 EVACUATION_POLICY, CONTACT_POLICY, FRESHNESS, FEATURES (+ v0 params)
  forecast_input.py  Per-location fire arrival estimates: Deepfire fire-spread or a labelled recorded/synthetic file
  snapshot.py    Producer: build_snapshot(), asset_exposure(), validate_snapshot(), read/write
  fire_input.py  Fire updates: Deepfire poll or recorded responses -> FireUpdate; dedupe; data_status; latency
  priority.py    Consumer: SnapshotSequence guard, apply_overrides(), rank_snapshot(), queues (evacuation window)
  tasks.py       SQLite TaskStore: tasks, roster, confirmed overrides, snapshot bookkeeping, events
  agent.py       Six tools (get_asset, lookup_facility, lookup_notability, lookup_valuation_reference,
                 propose_update, escalate) + investigate loop
  llm.py         NebiusLLM, AnthropicLLM and FakeLLM (same .create interface); live_llm() picks by key
  app.py         Streamlit: map, ranked table, review queue, details + timing breakdown, tasks, change log
  feeds.py       HTTP/cache layer, Gencat registers, Open-Meteo, DeepfireClient (unchanged API)
  notability.py  Wikipedia/Wikidata notability corpus: fetch_notability() (network, via the feeds cache)
                 and lookup() (offline search of fixtures/notability.json)
  valuation_reference.py  Quotable cost references for the custom-valuation layer: lookup() over
                 fixtures/valuation_references.json, offline only (no network half, unlike notability.py)
  --- v0 modules, gated by config.FEATURES, not used by the v4 path by default ---
  grid.py spread.py exposure.py decide.py routing.py fire_state.py scenario.py
fixtures/
  assets.json               12 synthetic assets_in rows (v0 shape, still the synthetic scenario input)
  snapshots/                Committed v4 snapshots: synthetic_gavarres_0001.json, _0002.json (+ gavarres_real_0001..0004)
  fire/                     Recorded provider responses used by fire_input.load_recorded()
  forecast/                 Labelled synthetic forecast-input-1 files for the synthetic scenarios (section 2.5)
  wind/                     Real recorded Open-Meteo previous-runs wind (ecmwf_ifs025, _previous_day1 slice) per
                            gavarres_real snapshot hour; drives the labelled CA enrichment of section 2.2
  teams.json                Fixture roster (section 5)
  evidence.json             Cached facility pages / register rows for lookup_facility (section 6)
  notability.json           Wikipedia/Wikidata intro extracts for named institutions, quotable offline
                            evidence for the per-asset criticality layer (fireline/notability.py)
  valuation_references.json Quotable cost lines (published tables and labelled project assumptions) for the
                            per-asset custom-valuation layer (fireline/valuation_reference.py)
  real_area/                Cached Equipaments/schools extract for the fixed Gavarres area (section 2.4)
scripts/
  make_snapshots.py         Build fixture + real-area snapshots (no network)
  fetch_data.py             Real data into data/ (network)
  investigate.py            One agent investigation: live call if an LLM API key is set, else labelled prerecorded
tests/                      pytest, no network, no LLM
```

## 1. Conventions

- Times: ISO 8601 UTC strings in JSON (`2026-07-03T08:00:00+00:00`), tz-aware `datetime` in Python.
- Coordinates exchanged as WGS84 (`longitude`, `latitude`; GeoJSON is lon/lat order). Distances in
  metres computed in EPSG:25831 (`grid.lonlat_to_xy`).
- Unknown is `null`, never zero. Every key listed for a record is always present, except the optional
  key groups of section 2.2, each present in full or not at all: the eight value-at-risk keys, the three
  criticality keys and the six custom-valuation keys.
- Records are plain dicts; pandas only in the UI.
- Stable ids: `asset_id = f"{source}:{source_id}"` (e.g. `fixture:can_xic`, `equipaments:3620043`,
  `schools:17001234`). Coordinates are attributes, not identity.

## 2. Snapshot (readme 5) — producer `fireline/snapshot.py`

### 2.1 Envelope

```python
{
  "schema_version": "1.1",       # 1.0 files still load; their v1.1 keys read as null
  "scenario_id": str,          # e.g. "synthetic_gavarres", "gavarres_2026-07"
  "incident_id": str,          # provider incident/cluster id or fixture id
  "snapshot_id": str,          # f"{scenario_id}-{sequence:04d}"
  "sequence": int,             # monotonically increasing within scenario_id, starts at 1
  "as_of": str,                # information cutoff (UTC)
  "computed_at": str,          # calculation completion (UTC)
  "input_mode": "live" | "recorded" | "synthetic",
  "fire_observed_at": str | None,
  "fire_source": str | None,   # e.g. "deepfire:satellite-perimeters", "fixture:synthetic_ignition"
  "fire_geometry": GeoJSON geometry | None,   # Polygon/MultiPolygon footprint, or Point when only a hotspot centre
  "fire_geometry_kind": "perimeter" | "hotspot_centre" | "simulated" | None,   # a hotspot centre is never shown as a
                               # perimeter; "simulated" = a model burned area (fire-spread run), not an observed perimeter
  "data_status": "current" | "stale" | "unavailable",
  "metrics": {"source_age_s": float | None, "processing_s": float | None},   # readme 11 latency, separate numbers
  "assets": [ <asset record> ... ]           # complete matching set for the fixed area, stable across updates
}
```

### 2.2 Asset record (all keys present)

```python
{
  "asset_id": str, "name": str, "asset_type": str,     # asset_type in VALUE_POLICY["by_type"] or "unknown"
  "latitude": float | None, "longitude": float | None,  # both null when unresolved
  "geometry": GeoJSON | None, "area_m2": float | None,
  "capacity": int | None, "estimated_occupancy": int | None,
  "occupancy_basis": str | None,           # "register capacity as proxy", "register headcount",
                                           # "enrolled pupils <curs> (<register>); staff not included",
                                           # "analyst override", ...
  "value_score": float | None, "value_basis": str | None,   # from config.VALUE_POLICY; basis = policy version string
  "distance_to_fire_m": float | None, "intersects_fire": bool | None,   # min distance to fire_geometry; 0 on overlap
  "burn_probability": float | None,
  "arrival_p10_at": str | None, "arrival_p50_at": str | None,
  "forecast_horizon_at": str | None, "forecast_source": str | None,
  "fire_arrival_at": str | None, "fire_arrival_basis": str | None,   # v1.1: selected arrival estimate + its semantics
  "evacuation_min": float | None, "evacuation_source": str | None,   # v1.1: total evacuation duration (minutes) + basis
  # per-asset criticality (optional as a group, FEATURES["asset_criticality"]): all three null together
  "criticality_tier": str | None,                   # config.CRITICALITY_POLICY["tiers"]; analyst-confirmed only
  "criticality_factors": [str] | None,              # closed enum, CRITICALITY_POLICY["factors"]
  "criticality_basis": str | None,                  # policy version + the confirming override
  # per-asset custom valuation (optional as a group, FEATURES["custom_valuation"]): all six null together
  "custom_value_eur_low": float | int | None, "custom_value_eur_mid": float | int | None,
  "custom_value_eur_high": float | int | None,      # a band, never a point; analyst-confirmed only
  "custom_value_method": str | None,                # config.CUSTOM_VALUATION_POLICY["methods"]
  "custom_value_components": [ {"label": str, "amount_eur": float} ] | None,   # the priced parts, [] for not_valued
  "custom_value_basis": str | None,                 # policy version + method + the confirming override
  # value-at-risk layer (optional, FEATURES["value_at_risk"]): all eight keys present together or all absent
  "replacement_value_eur": float | int | None, "replacement_value_basis": str | None,   # config.VALUE_AT_RISK_POLICY; null together
  "expected_loss_eur_low": float | None, "expected_loss_eur_mid": float | None, "expected_loss_eur_high": float | None,
  "people_exposed": float | None,                                   # estimated_occupancy x burn_probability
  "people_at_risk_p50": int | None, "people_at_risk_p10": int | None,   # whole estimated_occupancy, or 0
  "needs_review": bool, "review_reasons": [str],   # location_unknown, occupancy_unknown, occupancy_seasonal,
                                                   # class_ambiguous, value_unknown, exposure_unknown,
                                                   # forecast_unavailable, evacuation_unknown (v1.1),
                                                   # criticality_unassessed, valuation_unassessed
  "sources": [ {"fields": [str], "source": str, "observed_at": str | None, "available_at": str | None,
                "fetched_at": str | None, "notes": str | None} ],
  "municipality": str | None,             # convenience, not in readme; may be null
}
```

Rules: `needs_review` is true iff `review_reasons` is non-empty. `exposure_unknown` when
`distance_to_fire_m` is null (no location or no fire geometry). `value_unknown` when `asset_type` is not
in the value policy. `estimated_occupancy` null with `capacity` set is allowed; the basis string then
says capacity is a proxy only where the producer chose to fill `estimated_occupancy` from it (it does
not by default). School occupancy is the Gencat enrolment register joined on `codi_centre`
(`occupancy_source: "enrolment"` on the input row): pupils are a headcount, so they fill
`estimated_occupancy`, and the `sources` entry for the occupancy fields names that register
(`occupancy_register` / `occupancy_period` / `occupancy_fetched_at` on the input row) rather than the
register the identity fields came from. Forecast fields are filled by `forecast_input.attach_forecast` when `build_snapshot` is
given a `forecast=` (section 2.5), or by the CA raster (`snapshot._enrich_forecast`, `arrival=`) when
`config.FEATURES["forecast_enrichment"]` is on in the `cfg` passed to `build_snapshot`
(`forecast_source` then names the method, `"ca_ensemble (labelled enrichment, not validated)"`, and
`fire_arrival_basis` is `"p10 (ca_ensemble, labelled enrichment, not validated)"`); otherwise they are
null. The global flag stays False; `scripts/make_snapshots.py` passes a cfg shim with it on for every
real snapshot, and `build_snapshot`'s gate then applies the raster only where no provider forecast
covers that snapshot (`gavarres_real_0001..0003`; `0004` has one, and the two are never combined).
A CA arrival is a provided per-location estimate with source
semantics, so the timing rules of this section apply to it unchanged.
Point fallback for distance is recorded in `sources` with `fields: ["distance_to_fire_m", "intersects_fire"]` and a note
`"point fallback: facility footprint missing"`.

v1.1 timing fields (readme 5.2 and 6). `fire_arrival_at` is the producer's selected spread-predicted
arrival for this location; `fire_arrival_basis` states its semantics (`"p10"`, else `"p50"`, else the
provider's declared basis for its single estimate; section 2.5). It is never inferred from distance:
when no forecast covers the location it is null and `review_reasons` carries `forecast_unavailable`.
`forecast_source` and `forecast_horizon_at` are required provenance whenever `fire_arrival_at` is set,
and a `sources` entry lists the forecast fields. `evacuation_min` is the total estimated evacuation
duration (mobilisation + preparation/loading + movement to a receiving location) with
`evacuation_source` naming its basis: by default `config.EVACUATION_POLICY` by class (`"evacuation-proto-…"`,
a labelled prototype assumption with its component minutes and assumptions in the `sources` note), or
`"analyst override: …"` after confirmation. Unknown class -> null and `evacuation_unknown`.

Value-at-risk layer (readme 5.2, handoff 002), behind `config.FEATURES["value_at_risk"]`, off by
default. `snapshot.VALUE_AT_RISK_KEYS` names the eight keys in record order; they are present in full
or absent in full, `schema_version` stays `1.1`, and a snapshot built with the flag off is
byte-identical to one built before the layer existed. All eight are in `_COMPUTED_FIELDS`, so a v4
record fed back into `build_snapshot` is recomputed and its stale value provenance dropped.
`derive_value_at_risk` fills them after the forecast pass, never from distance:

```
people_exposed         = estimated_occupancy x burn_probability          # rounded to 1 decimal; capacity is never a headcount
people_at_risk_p50     = estimated_occupancy if slack_p50 <= 0 else 0
people_at_risk_p10     = estimated_occupancy if slack_p10 <= 0 else 0
expected_loss_eur_low  = burn_probability x d_low x replacement_value_eur    # whole euros; _mid and _high alike
slack_pXX              = arrival_pXX_at - evacuation_min - CONTACT_POLICY["buffer_min"] - now_at
```

`slack_pXX` is the section 4 window evaluated at each arrival quantile instead of at the selected
arrival, and the threshold is `<= 0`, the `window_exhausted` boundary, because `people_at_risk`
re-labels that status weighted by headcount. It counts the whole headcount and does not model partial
clearance. `replacement_value_eur` and the damage ratios `d_low` / `d_mid` / `d_high` come from
`config.VALUE_AT_RISK_POLICY` by class (`value_basis: "assumed"`): rounded per-class placeholders for a
per-asset figure, not a valuation, and the band is the damage-ratio band only, so the value uncertainty
is at least as large. The euros are total economic loss, insured and uninsured, not an insurer's figure,
and no consumer may sort, filter or rank on them (readme 5.2). `value_score` is unrelated and unchanged.

Every field is `null`, never zero, when an input is null: no `estimated_occupancy` (including a
`capacity`-only row), no `burn_probability`, no `evacuation_min`, no `forecast_source`, no location, or a class
with no `replacement_value_eur` (`nucleus`, or any class outside the policy). `burn_probability = 0.0` is
a value and gives zeros; an asset a forecast covers whose quantile is null is not reached inside the
horizon, so `people_at_risk_* = 0` rather than null. An unusable arrival timestamp leaves it null. An
asset whose class is in the policy and has at least one non-null field carries one `sources` entry
naming exactly the fields it set,
`source: "config.VALUE_AT_RISK_POLICY"`, with the formulas, the band and those caveats in its note; it
replaces any earlier entry for those fields, so re-deriving after an override does not stack provenance.
No review reason is added for a class that is not valued: `REVIEW_REASONS` is unchanged, and the count
of assets excluded for null inputs carries that message instead.

`validate_snapshot` checks, when the keys are present: `replacement_value_eur` nonnegative and finite,
null iff `replacement_value_basis` is null; `people_exposed` nonnegative and finite, null iff
`estimated_occupancy` or `burn_probability` is null; `people_at_risk_*` a nonnegative int equal to `0` or
to the whole `estimated_occupancy`, and non-null only with `estimated_occupancy`, `evacuation_min` and
`forecast_source` all set; `expected_loss_eur_*` nonnegative and finite, null iff `burn_probability` or
`replacement_value_eur` is null, and `expected_loss_eur_low <= _mid <= _high` when all three are set.
Records without the keys (schema 1.0 and 1.1 files alike) stay valid.

Per-asset criticality and custom valuation (readme 4 and 5.2, handoffs 003 and 004), behind
`config.FEATURES["asset_criticality"]` and `config.FEATURES["custom_valuation"]`.
`snapshot.CRITICALITY_KEYS` names three keys and `snapshot.CUSTOM_VALUATION_KEYS` six; each group is
present in full or absent in full, and within a group the keys are null together on an asset nobody has
assessed. The intent is the v1.1 timing keys' one: a snapshot written before either layer existed stays
valid. *Known gap (2026-09-20):* `validate_snapshot`'s missing-key check exempts `CRITICALITY_KEYS` only,
so a snapshot written before the custom-valuation layer is reported as `missing keys` for all six until it
is rebuilt, and `_custom_valuation_errors`' all-absent branch is unreachable (handoff 004, known limits). **The producer
never asserts either one.** A tier and a bespoke euro figure reach an asset only through an
analyst-confirmed override (section 4: `priority.OVERRIDE_FIELDS` carries `criticality_tier` and
`custom_valuation`), so `make snapshots` stays deterministic and LLM-free. With a flag on, an asset of an
assessed class carries `criticality_unassessed` / `valuation_unassessed` in `review_reasons` until an
analyst confirms one; both are in `agent.REVIEW_REASONS`.

`custom_valuation` is the name of the override, not of a field: its payload `{"method", "components",
"amount_eur_low", "amount_eur_mid", "amount_eur_high", "note"}` is validated by
`priority.custom_valuation_value` and fanned out into the six keys. The guards, all from
`config.CUSTOM_VALUATION_POLICY`, exist so a bespoke figure cannot be softer than the class figure it
replaces: `method` is a closed enum (`component_replacement`, `service_continuity`,
`irreplaceable_holdings`, `parent_institution_scaled`, `not_valued`); `not_valued` is the ordinary answer
and carries no amounts and no components, recording that the agent looked; every other method needs
`0 <= low <= mid <= high` with `high >= min_band_ratio x low` (1.5), at least `min_components` priced
components, and for `component_replacement` components summing to `amount_eur_mid` within
`component_sum_tolerance` (5%); `amount_eur_high` above `max_eur` is refused as a misplaced decimal point,
not judged. `assess_classes` is exactly the four classes `VALUE_AT_RISK_POLICY` has no row for
(`research_facility`, `university`, `aerodrome`, `fire_station`); a class the table does price keeps the
class answer.

What a confirmed valuation changes, and all it changes. In `snapshot.derive_value_at_risk`,
`custom_value_eur_mid` replaces the class `replacement_value_eur` and `replacement_value_basis` names the
per-asset valuation and its method, and `expected_loss_eur_<level>` becomes `burn_probability x d_<level>
x custom_value_eur_<level>` with the damage ratios from `CUSTOM_VALUATION_POLICY["damage_ratio"]`
(0.10 / 0.35 / 0.75, since these classes have no class row) — so the band compounds the valuation
uncertainty with the damage uncertainty instead of stating the damage band alone. `custom_value_eur_mid`
also orders `priority.strategic_queue` after the tier. It reaches nothing else: `priority.ranked_sort_key`
and `contact_priority.contact_sort_key` do not read it, and `scripts/validate.py` `check_valuation`
measures that a huge confirmed valuation on every asset leaves the ranked contact order byte-identical.
Nothing here is a market valuation or an insurer's figure; a figure is only as good as the reference the
agent quoted and the analyst accepted.

`validate_snapshot` checks, when the six keys are present (all six, or none): `custom_value_method` in the
policy enum; a non-empty `custom_value_basis` whenever a method is set, and everything null when it is
not; `not_valued` carrying no amounts and no components; otherwise a full band of nonnegative finite
numbers with `custom_value_eur_low <= _mid <= _high`; and components that are `{"label", "amount_eur"}`
objects with a non-empty label and a nonnegative finite amount. Whether the figure is *right* is the
analyst's call, not a validation.

### 2.3 API

```python
snapshot.asset_exposure(lon, lat, geometry, fire_geometry) -> (distance_m | None, intersects | None, note | None)
snapshot.build_snapshot(assets_in, fire, *, scenario_id, incident_id, sequence, as_of, input_mode,
                        computed_at=None, data_status=None, metrics=None, arrival=None, forecast=None,
                        cfg=config) -> dict
    # assets_in: v0 assets_in rows (asset_id, name, asset_class, lon, lat, municipality, occupancy, ...) or
    #            v4 records (asset_type, latitude, longitude, capacity...) — both accepted, v0 keys mapped.
    # fire: fire_input.FireUpdate dict or None (None -> fire fields null, data_status "unavailable").
    # arrival: optional spread.ArrivalRaster; used only when FEATURES["forecast_enrichment"].
    # forecast: optional forecast-input-1 dict (section 2.5); takes precedence over `arrival`.
    # cfg.FEATURES["value_at_risk"]: adds the eight value-at-risk keys after the forecast pass.
snapshot.derive_value_at_risk(rec, now_at=None, cfg=config) -> None
    # fills snapshot.VALUE_AT_RISK_KEYS on one record in place from its own fields (section 2.2).
    # now_at: the snapshot `as_of`, the epoch for people_at_risk_*; None derives only the
    # time-independent fields (replacement_value_*, expected_loss_*, people_exposed) and leaves
    # people_at_risk_* as it found them, rather than computing them from a wrong epoch.
snapshot.VALUE_AT_RISK_KEYS                       # the eight keys, in record order
snapshot.validate_snapshot(snap) -> list[str]     # [] when valid; messages otherwise
snapshot.write_snapshot(snap, path) / snapshot.read_snapshot(path) -> dict
```

Fixtures: `fixtures/snapshots/synthetic_gavarres_0001.json` (sequence 1, fire at 08:00) and
`synthetic_gavarres_0002.json` (sequence 2, fire advanced; the window order changes and some windows are
exhausted; one asset with `location_unknown`, two with `occupancy_unknown`, one with `class_ambiguous`,
one located asset without a forecast). Both `input_mode: "synthetic"` with the synthetic forecasts of
`fixtures/forecast/`. `gavarres_real_0001..0003.json` are the real facilities with real recorded July
perimeters; no provider forecast covers that incident, so their arrivals are a labelled model enrichment:
the v0 CA ensemble (`spread.run_ca`, `config.CA`, uncalibrated, no fuel or slope, 100 m cells) seeded
from each real perimeter with the real recorded wind of `fixtures/wind/` (one sample per snapshot, the
Open-Meteo previous-runs value for the hour containing `as_of`), n_runs 20, horizon 720 min, seed 0.
Assets reached within 12 h (18, 65, 94 of 99 located) carry
`fire_arrival_at = arrival_p10_at`, the labelled basis and source strings above, `forecast_horizon_at`,
`burn_probability` and a `sources` entry; the rest are `forecast_unavailable`. The CA spreads much
faster than the real fire did (p50 burned area about 10,000 ha at 12 h against the real 1,263 -> 3,867 ha
over about 17 h), so these arrivals are a pipeline demonstration, not validated predictions.
`gavarres_real_0004.json` is the real facilities with a real recorded Deepfire fire-spread run of
2026-09-19 seeded at the July incident centroid (`fire_geometry_kind: "simulated"`), which covers no
facility within its 12 h horizon: its 99 located assets carry `burn_probability = 0.0` with the run as
their `forecast_source`, the 69 unlocated ones keep null, and every asset stays `forecast_unavailable`
for want of an arrival. All committed snapshots are built with `value_at_risk` on, so their records carry
the eight keys of section 2.2.

### 2.5 Forecast input — `fireline/forecast_input.py`

Per-location fire arrival estimates in the `forecast-input-1` shape (module docstring has the full
format): `forecast_source`, `input_mode` (`synthetic` | `recorded` | `live`), `issued_at`,
`forecast_horizon_at`, `basis`, `note` and `estimates: {asset_id: {arrival_p10_at, arrival_p50_at,
burn_probability, arrival_at?}}`. Synthetic files must say "synthetic, not a provider forecast" in
their note.

```python
forecast_input.load_forecast(path) -> dict                      # validated; ValueError otherwise
forecast_input.attach_forecast(assets, forecast) -> None        # in place: forecast fields, fire_arrival_at/basis, sources entry;
                                                                # uncovered or unlocated assets get forecast_unavailable
forecast_input.deepfire_spread_to_forecast(body, received_at, asset_points, *, min_burn_probability=None,
                                           input_mode="recorded") -> dict
    # body: a COMPLETED /v1/fire-spread/simulations/{id} response (hourly cumulative burned-area polygons;
    # ensemble bands with burn_probability). asset_points: {asset_id: (lon, lat)}; EVERY one gets an estimate.
    # arrival_at = createdAt + elapsed_seconds of the first hour whose polygon (union of bands >=
    # min_burn_probability, default 1/N) covers the point, else null. burn_probability = the maximum band
    # probability covering the point at any hour within the horizon, else 0.0; every band is scanned,
    # including those below min_burn_probability, which selects only the arrival union.
    # horizon = createdAt + durationHours. t0 = createdAt is an assumption (undocumented).
forecast_input.forecast_from_recorded_spread(path, asset_points, *, min_burn_probability=None) -> dict
```

The fire-spread adapter returns an estimate for every located asset it is given, not only the covered
ones, and `burn_probability = 0.0` and `burn_probability = null` mean different things: **`0.0` means the
run covers that location and puts no burned area there within its horizon; `null` means no forecast covers
it at all.** Neither is derived from distance; both come from containment only. A single-member
(deterministic) run carries no band probability, so a covered point is `1.0` and an uncovered one `0.0`.
An estimate with `burn_probability` but no `arrival_at` still leaves the asset `forecast_unavailable`
after `attach_forecast`, with `burn_probability`, `forecast_horizon_at` and `forecast_source` set and
listed in its `sources` entry; `fire_arrival_at` stays null. Raising `min_burn_probability` can therefore
move an arrival but never lowers a location's `burn_probability`.

Selection order for `fire_arrival_at`: p10 (`fire_arrival_basis` `"p10"`), then p50 (`"p50"`), then
`arrival_at` (basis = the forecast's `basis` string, e.g. the Deepfire isochrone-crossing label), else
null. Deepfire fire-spread runs are seeded from hotspots observed within the last `lookbackHours` of
the request time, so they exist only for current fires; the July incident cannot be re-run
(`fixtures/fire/deepfire/README.md`).

### 2.4 Real-area assets

`fixtures/real_area/assets_gavarres.json`: envelope `{area, bbox, extracted_on, registers, counts, assets}` whose
`assets` hold every `data/assets_in.json` row (Gencat Equipaments +
schools extract, 2026-09-19) inside `feeds.GAVARRES_BBOX`, in v4 asset-record shape with `sources`
carrying `fetched_at` = extraction time and `source` = the register. Schools carry
`estimated_occupancy` = enrolled pupils from the Gencat enrolment register (`xvme-26kg`, sourced
separately in `sources`); the music, dance and adult-education centres it does not list keep
`occupancy_unknown`. Unlocated care homes and
campsites from `data/unlocated.json` inside the area's municipalities are included with null
coordinates and `location_unknown`. `fixtures/real_area/README.md` records counts and the limitation
(care homes and campsites carry no coordinates in the registers).

## 3. Fire input — `fireline/fire_input.py`

```python
FireUpdate = {
  "provider": "deepfire" | "fixture",
  "incident_id": str,
  "observed_at": str | None,       # provider observation time
  "received_at": str,              # when we got the response (UTC)
  "geometry": GeoJSON | None,      # Polygon/MultiPolygon, or Point for a hotspot centre
  "geometry_kind": "perimeter" | "hotspot_centre" | None,
  "source": str,                   # "deepfire:satellite-perimeters", "deepfire:clusters", "fixture:..."
  "raw_ref": str | None,           # path of the cached raw response
  "notes": str,                    # e.g. "observed_at from observed_watermark"; informational
}

fire_input.parse_deepfire(body: dict, received_at: datetime, collection: str) -> FireUpdate | None
fire_input.poll_deepfire(client, bbox, as_of, *, incident_id=None) -> FireUpdate | None   # live path
fire_input.load_recorded(dir_or_files) -> list[FireUpdate]      # recorded responses through parse_deepfire
fire_input.UpdateCache().accept(update) -> bool                 # False for duplicate (incident_id, observed_at, geometry hash)
                                                                #   and for an update observed earlier than the latest accepted
fire_input.record_response(collection, body, received_at, out_dir) -> Path   # writes the recorded shape
fire_input.measure_update(update, started_at, finished_at, now=None) -> {"source_age_s", "processing_s"}
fire_input.data_status(observed_at, now, cfg=config) -> "current" | "stale" | "unavailable"
fire_input.source_age_s(observed_at, now) -> float | None
fire_input.Stopwatch() -> .start(); .stop() -> processing_s     # receipt-to-snapshot processing time
fire_input.synthetic_update(fire_state) -> FireUpdate           # v0 FireState -> FireUpdate (perimeter -> WGS84 GeoJSON)
```

Thresholds in `config.FRESHNESS = {"stale_after_s": 3600, "unavailable_after_s": 21600}`. Recorded
responses live in `fixtures/fire/deepfire/*.json` as `{"collection", "received_at", "body"}`.

## 4. Priority — consumer `fireline/priority.py`

Contact priority is the remaining evacuation window (readme 6), policy `config.CONTACT_POLICY`
(`forecast-evacuation-window-v2`). The weighted proximity/size/value score of v1.0 is removed.

```python
priority.SnapshotSequence().accept(snap) -> bool   # False on duplicate snapshot_id or sequence <= last for scenario_id
priority.apply_overrides(assets, overrides, cfg=config, now_at=None) -> list[dict]   # copies; overrides from tasks.TaskStore.overrides()
    # now_at (the snapshot as_of, passed by rank_snapshot) is the epoch for re-derived people_at_risk_*
priority.rank_asset(asset, now_at, cfg=config) -> dict      # adds the coordination keys below (priority_rank stays None)
priority.rank_snapshot(snap, cfg=config, overrides=None, now_at=None) -> {"ranked": [...], "needs_review": [...], "flagged": [...], "all": [...], "now_at": str, "policy": {...}}
    # now_at defaults to snap["as_of"] (CONTACT_POLICY["now"]); flagged = ranked assets that still carry review reasons
priority.input_age(asset, now=None) -> {"oldest_observed_at", "newest_fetched_at", ...ages when now given}
priority.window_bucket(slack_min, cfg=config) -> None | "exhausted" | "small" | "open"   # small: <= CONTACT_POLICY["attention_min"]
```

Added keys per asset:

| Key | Type | Meaning |
|---|---|---|
| `priority_rank` | int or null | 1-based position in `ranked`; null in `needs_review`. |
| `queue` | `"ranked"` or `"needs_review"` | Ranked only when every timing input is known. |
| `priority_status` | `"window_open"`, `"window_exhausted"` or `"needs_review"` | Exhausted = `slack_min <= 0`: immediate analyst review, not an evacuation instruction. |
| `time_to_impact_min` | float or null | `fire_arrival_at - now_at` in minutes. |
| `latest_start_min` | float or null | `fire_arrival_at - evacuation_min - buffer_min - now_at` in minutes. |
| `slack_min` | float or null | Remaining window `latest_start_min - 0` (i.e. relative to `now_at`); negative allowed. |
| `window_components` | dict | `{"fire_arrival_at", "fire_arrival_basis", "forecast_source", "forecast_horizon_at", "now_at", "evacuation_min", "evacuation_source", "buffer_min", "distance_to_fire_m"}` as used. |
| `priority_policy_version` | str | `CONTACT_POLICY["version"]`. |
| `priority_reasons` | [str] | Human-readable explanation lines: each timing component, the window arithmetic, `"needs review: …"` when unranked, and `"review flag: <reason>"` for each producer review reason. |

Ranked order: `slack_min` ascending, then `fire_arrival_at` ascending, then `distance_to_fire_m`
ascending (null last), then `asset_id`. Missing `fire_arrival_at`, `evacuation_min`,
`forecast_source` or `evacuation_source` -> `queue = "needs_review"`, `priority_status =
"needs_review"`, timing keys null, and `forecast_unavailable` / `evacuation_unknown` added to
`priority_reasons` (and to `review_reasons` if the producer did not already set them). A missing
distance does not prevent ranking. Needs-review order is unchanged from v1.0: exposure unknown first,
then ascending known distance, then `asset_id`. Assets with review reasons but complete timing stay
in `ranked` and also carry their reasons (the UI shows them in both views).

The ordering and window arithmetic are the same as `contact_priority.rank_contacts` (section 16 of
the readme) with timestamps converted to minutes from `now_at`; a test asserts agreement.

`apply_overrides` behaviour from v1.0 is kept (occupancy/capacity, `asset_type` re-derives
`value_score`, `override_conflict`). v1.1 adds field `evacuation_min`: sets `evacuation_source =
"analyst override: <source>"` and clears `evacuation_unknown`. An `asset_type` override also re-derives
the policy evacuation duration for the confirmed class (with a `config.EVACUATION_POLICY` sources entry)
unless an analyst-confirmed duration is already in place. Overrides on `fire_arrival_at` are not
accepted (forecasts come from the producer). On an asset that carries the optional value-at-risk keys
(section 2.2) every override also re-derives them through `snapshot.derive_value_at_risk`, so a confirmed
class, headcount or evacuation duration moves the euros and the people counts with it; without `now_at`
only the time-independent fields move and `people_at_risk_*` is left untouched.

## 5. Tasks and roster — `fireline/tasks.py` (SQLite)

Roster fixture `fixtures/teams.json`: `[{"team_id", "name", "capabilities": [str], "available": bool,
"source": "fixture"}]`. Capability tags: `occupancy_check`, `facility_contact`, `access_check`,
`resource_request`, `medical`, `transport`.

Actions (readme 7): `confirm_occupancy`, `contact_facility`, `check_access`, `request_resources`.
Default required capabilities: `{"confirm_occupancy": ["occupancy_check"], "contact_facility":
["facility_contact"], "check_access": ["access_check"], "request_resources": ["resource_request"]}`.

```python
class TaskStore:                       # tasks.TaskStore(path=":memory:" | Path)
    load_roster(path) / teams() -> list[dict]
    suggest_tasks(scored_assets, snapshot_id) -> list[task]   # from review reasons; dedupes open (asset_id, action, reason)
    create_task(asset_id, action, reason, *, required_capabilities=None, snapshot_id, notes="") -> task
    get(task_id) / tasks(asset_id=None, status=None) -> list[task]
    assign(task_id, team_id) -> task          # raises AssignmentError("team busy: ...") / ("capability mismatch: ...")
    release(task_id) -> task                  # unassign, back to open
    set_status(task_id, status, note="") -> task     # open|assigned|in_progress|blocked|done; done only by analyst
    add_question(task_id, question) / answer_question(task_id, index, answer)
    set_deadline(task_id, deadline_at, basis)
    confirm_override(asset_id, field, value, *, source, snippet, url=None, observed_at=None, confidence, proposal_id=None) -> override
    overrides(asset_id=None) -> list[override]      # {override_id, asset_id, field, value, previous, source, snippet, url, observed_at, confidence, confirmed_at}
    apply_snapshot(snap) -> {"accepted": bool, "affected_task_ids": [...], "missing_asset_ids": [...], "changed": [...]}
        # records snapshot_id; flags open tasks whose asset's distance/intersects/needs_review/fire_arrival_at/
        # priority_status changed or whose remaining window crossed an attention bucket (priority.window_bucket)
        # ("affected_by_snapshot" = snapshot_id on the task); missing assets keep their tasks and get an event
    events(limit=None) -> list[{"at", "kind", "asset_id", "task_id", "message"}]     # the change log
```

Task record: `task_id, asset_id, action, reason, status, required_capabilities, assigned_team_id,
deadline_at, deadline_basis, blocking_questions (list of {question, answer}), evidence (list),
notes, created_at, updated_at, based_on_snapshot_id, affected_by_snapshot_id (nullable), suggested (bool)`.
A team with any task in `assigned|in_progress|blocked` is busy. `request_resources` may stay
unassigned and blocked.

## 6. Agent — `fireline/agent.py`

Six tools, each a plain function with a JSON schema in `TOOLS`:

```python
get_asset(asset_id, workbench) -> dict            # scored asset record (trimmed, numbers rounded) + open tasks + overrides
lookup_facility(query, municipality=None) -> list[dict]   # fixtures/evidence.json + fixtures/registers.json + data/registers/*.json
    # candidate: {evidence_id, name, municipality, register|url, capacity, snippet, observed_at, fetched_at, score, fields}
lookup_notability(query, limit=3) -> list[dict]           # fixtures/notability.json; the criticality question only, never the network
lookup_valuation_reference(asset_type=None, query="", limit=4) -> list[dict]   # fixtures/valuation_references.json; quotable cost lines
    # reference: {reference_id, applies_to, title, statement, unit, amount_eur, basis, source, url, observed_at}
    # basis "published" (cited external table) or "assumed" (project placeholder, marked [assumed] inside the statement)
propose_update(asset_id, field, value, source, quoted_snippet, confidence, url=None, observed_at=None, workbench) -> proposal
    # field in agent.PROPOSAL_FIELDS = ("estimated_occupancy", "capacity", "asset_type", "evacuation_min",
    #                                   "criticality_tier", "custom_valuation"); NOT applied; status "pending"
    # criticality_tier value: {"tier", "factors"}; custom_valuation value: {"method", "components",
    #   "amount_eur_low/mid/high", "note"} — both validated by the same priority.* function an analyst confirmation uses
escalate(asset_id, question, options, default, workbench) -> question record (status "open"); nothing applied
```

`Workbench` (in agent.py): `assets: dict[asset_id -> scored asset]`, `tasks: TaskStore | None`,
`proposals: list`, `questions: list`, `change_log: list[str]`. `agent.investigate(workbench, asset_id,
llm=None, max_steps=agent.DEFAULT_MAX_STEPS) -> record` (8, not 6: one asset can carry
`occupancy_unknown`, `criticality_unassessed` and `valuation_unassessed` at once) with `tool_calls, final_text, proposals_added, questions_added,
postcheck_ok, llm_mode: "live"|"fake"|"prerecorded"`. Number post-check kept. Confirmation is the
analyst's: `tasks.TaskStore.confirm_override(..., proposal_id=)` then rescoring; `agent.confirm_proposal
(workbench, proposal_id)` does that and marks the proposal `confirmed`; `reject_proposal` marks it
`rejected`. The agent never assigns teams or changes policy.

Evidence cache `fixtures/evidence.json`: `[{"evidence_id", "name", "municipality", "url", "snippet",
"observed_at", "fetched_at", "capacity", "asset_type", "notes", "source", "register"?, "address"?}]`,
labelled manual enrichment (`source: "manual enrichment"`) or copied register rows (`source:
"gencat:<register>"`). Candidates from `lookup_facility` always carry both `register` and `url` (one
null) plus `source` and `asset_type`. `propose_update` raises `CapacityAsOccupancyError` (surfaced by
`dispatch` as an error with a hint) when `estimated_occupancy` is proposed from capacity-worded evidence.
`fixtures/agent/prerecorded_investigation.json` holds one real-model transcript (or a labelled fake if
no key was available when recorded) that `scripts/investigate.py` replays when no key is present.

## 7. UI — `fireline/app.py`

Reads `fixtures/snapshots/` (and `data/snapshots/` when present) ordered by `sequence`; "Next update"
advances the sequence through `SnapshotSequence` + `TaskStore.apply_snapshot`, and "Previous" plus an
`as_of`-labelled slider move to any snapshot (`ui_state.Session.go_to`). A snapshot at or below the
store's accepted sequence is displayed without applying it (`view_only` in the result, `status()`
carries `applied_sequence` and `reviewing_earlier`): re-ranked with the confirmed overrides, with
tasks, events and the accepted sequence unchanged and no task suggestions from that earlier moment. Persists to
`data/fireline.sqlite` (path from `FIRELINE_DB`). Shows: input mode, `as_of`, `computed_at`,
`data_status`, source age and processing time; map with fire geometry (perimeter vs hotspot centre
styled differently) and assets coloured by queue/remaining window; ranked table; needs-review queue; selected
asset details with the timing breakdown (arrival, evacuation duration, buffer, window), input age and sources; proposals awaiting confirmation; task
controls (create, assign with roster check, progress, block, release); change log from `events()`.

Value at risk (section 2.2, handoff 002), when the snapshot carries the layer.
`ui_state.value_at_risk_totals(assets)` is the scenario aggregate, and `Session.status()["value_at_risk"]`
holds it for the assets on screen with the confirmed overrides applied, so a confirmed headcount or class
moves the totals. Its keys: `layer` (False when the snapshot has none of the eight keys, so the header
says the layer is off instead of reporting zeros), the sums `people_exposed`, `people_at_risk_p50`,
`people_at_risk_p10`, `expected_loss_eur_low` / `_mid` / `_high` over **located** assets with non-null
values, `located`, `excluded_people` and `excluded_eur` (located assets left out of each total for a null
input, counted separately because they differ), `covered` and `reached` (located assets with a
`forecast_source`, and of those the ones with a positive `burn_probability`), `horizon_at`, `enrichment`
(a `forecast_source` labelled unvalidated enrichment) and `policy_version`. It is the single
definition of the aggregate: `scripts/make_snapshots.py value_totals` calls it for the totals it writes
into the real-area README, so the header and that file cannot disagree.

A second header metrics row shows people exposed, people at risk at p50 (p10 in the tooltip), expected
loss mid with its band, and the excluded-asset count, each `help` naming what it excludes. The caption
under it states the totals and the known limits: assumed per-class values with no per-asset basis, the
damage-ratio band only, total economic loss rather than an insurer's figure, no partial clearance, and
the uncalibrated CA bias when the scenario's forecast is labelled enrichment. `covered` without `reached`
is reported as a zero, not a gap ("0 exposed within the N h horizon", the horizon derived from
`forecast_horizon_at` and `as_of`): the run covers those locations and puts no burned area there. The
assets still carry `forecast_unavailable`, which in that case means no arrival time; `REVIEW_REASONS` and
the review queue are unchanged.

The ranked and needs-review tables gain `people exposed`, `people at risk p50 / p10` and one expected-loss
column whose label names it an estimate from an assumed replacement cost with its damage-ratio band. That
column is a formatted **display string**, never a number, so a header click cannot order the table by it;
no euro field reaches a sort key, a filter or any ranking path, and the ranked order stays smallest
remaining window first. The selected asset adds `app.value_frame`: headcount x burn probability, the
replacement value and its basis, the three damage ratios, the three expected losses, and which arrival
quantile made each `people_at_risk` flag fire; the policy note itself stays in the asset's `sources`
entry rather than being repeated. Every one of these renders "-" when the layer is absent.

## 8. Feature flags — `config.FEATURES`

`{"spread_ca": False, "routing": False, "decisions": False, "forecast_enrichment": False,
"value_at_risk": False}`. `value_at_risk` gates the eight value-at-risk keys of section 2.2 in
`build_snapshot`; with it off the snapshot is byte-identical to one built before the layer existed. The v0
scenario pipeline (`scripts/precompute.py`, `scenario.Scenario`) remains runnable when the flags are on;
the v4 path never imports the raster stack unless `forecast_enrichment` is set. The globals stay False;
`scripts/make_snapshots.py` enables them through per-call cfg shims: `forecast_enrichment` for every
real snapshot, which enriches the ones no provider forecast covers (section 2.3 fixtures), and
`value_at_risk` for every committed snapshot, so the fixtures carry the layer while the library
default stays off. Forecast-driven tiers are optional context only.

---

## Appendix: v0 contract (engine behind the flags)

Kept verbatim from CONTRACTS v0 for the gated modules. See git history (`13e0b74`) for the full text:
Grid, FireState, ArrivalRaster, v0 asset table (`asset_class, lon, lat, occupancy, burn_prob,
arrival_p10_min, tier, needs_review: list[str]`), decide, routing, Scenario, seven-tool agent. The v4
agent replaces the seven tools; `scenario.py` gains `to_snapshot()` delegating to `snapshot.build_snapshot`.


## Voice interview display and assistance contract

`fireline.voice_models.CallRequest.location_display_name` is an optional private trusted
name/address (1–256 characters when present), separate from immutable request/asset/snapshot
IDs. Briefing and snapshot-contact adapters use supplied asset `name` or `address`, never
the asset ID as a display fallback. Existing persisted requests default to `None`; changing
a display name on an existing request still fails immutable-association checks.

`slng_voice.call_arguments` retains its seven original bindings and adds
`location_display_name` only when present. Generic and Sable packages advertise it as an
optional template variable with an empty default. Legacy deployed packages without that
binding reject named requests before dialing; update and verify the agent first. An absent
name causes a natural building/address question, never an invented place or spoken ID.

`voice_dialogue.READINESS_DIALOGUE` supplies shared direct questions and proactive mobility,
transport and uncertainty branches. The callback offer is exactly "Would you like an
emergency responder to call you to further assist you?" Offers do not establish
`wants_human`; only actual acceptance, refusal or a direct request supplies that evidence.
Unknown group coverage does not stop individual help collection. No callback answer clears
unresolved readiness needs or the existing human-review gate. No tool or model output alone
confirms dispatched resources, a callback arrangement, evacuation or arrival.
