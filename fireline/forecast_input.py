"""Per-location fire arrival estimates for the snapshot producer (CONTRACTS.md 2.2, v1.1 timing fields).

A forecast is a plain dict in the `forecast-input-1` shape, read from a labelled file
(`load_forecast`) or built by a provider adapter, and attached to asset records by `attach_forecast`.
The selected arrival (`fire_arrival_at`) is the provider's p10 when present, else its p50, else null;
`fire_arrival_basis` states which. Nothing in this module derives an arrival time, a quantile or a
probability from distance: assets that the forecast does not cover get null and the
`forecast_unavailable` review reason (readme section 4 "Spread forecast").

File format `forecast-input-1`:

    {
      "schema_version": "forecast-input-1",
      "forecast_source": str,                    # provider/method label copied to asset.forecast_source
      "input_mode": "synthetic" | "recorded",    # synthetic files are hand-designed, not a provider forecast
      "issued_at": iso,                          # when the estimates were issued (asset sources.observed_at)
      "forecast_horizon_at": iso,                # end of the forecast run; arrivals never exceed it
      "basis": "p10" | "p50" | <other>,          # the estimate semantics the file's author declares
      "note": str,                               # method and, for synthetic files, the design reasoning
      "estimates": {asset_id: {"arrival_p10_at": iso | null, "arrival_p50_at": iso | null,
                               "burn_probability": float | null}}
    }

The declared `basis` is provenance (it goes into the sources note); the selection rule is always p10,
then p50. Synthetic files must say "synthetic, not a provider forecast" in their note.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "forecast-input-1"
INPUT_MODES = ("synthetic", "recorded")
SYNTHETIC_LABEL = "synthetic, not a provider forecast"
FORECAST_UNAVAILABLE = "forecast_unavailable"
ESTIMATE_KEYS = ("arrival_p10_at", "arrival_p50_at", "burn_probability")
# Asset keys this module may set, in record order.
FORECAST_FIELDS = ("burn_probability", "arrival_p10_at", "arrival_p50_at", "forecast_horizon_at",
                   "forecast_source", "fire_arrival_at", "fire_arrival_basis")


# ------------------------------------------------------------------------------------------ time
def _utc(t) -> datetime | None:
    """ISO string or datetime -> tz-aware UTC datetime; None stays None. Raises ValueError on junk."""
    if t is None:
        return None
    if isinstance(t, datetime):
        return (t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t).astimezone(timezone.utc)
    if not isinstance(t, str):
        raise ValueError(f"not a timestamp: {t!r}")
    s = t.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).astimezone(timezone.utc)


def _iso(t) -> str | None:
    dt = _utc(t)
    return None if dt is None else dt.isoformat()


# ------------------------------------------------------------------------------------ validation
def validate_forecast(forecast) -> None:
    """Raise ValueError unless `forecast` is a well-formed forecast-input-1 dict."""
    if not isinstance(forecast, dict):
        raise ValueError("forecast is not a dict")
    if forecast.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version {forecast.get('schema_version')!r} != {SCHEMA_VERSION!r}")
    for key in ("forecast_source", "input_mode", "issued_at", "forecast_horizon_at", "basis", "note", "estimates"):
        if key not in forecast:
            raise ValueError(f"missing key {key}")
    if not isinstance(forecast["forecast_source"], str) or not forecast["forecast_source"].strip():
        raise ValueError("forecast_source must be a non-empty string")
    if forecast["input_mode"] not in INPUT_MODES:
        raise ValueError(f"input_mode {forecast['input_mode']!r} not in {INPUT_MODES}")
    if not isinstance(forecast["basis"], str) or not forecast["basis"].strip():
        raise ValueError("basis must be a non-empty string")
    if not isinstance(forecast["note"], str):
        raise ValueError("note must be a string")
    if forecast["input_mode"] == "synthetic" and SYNTHETIC_LABEL not in forecast["note"]:
        raise ValueError(f"a synthetic forecast note must say {SYNTHETIC_LABEL!r}")
    try:
        issued, horizon = _utc(forecast["issued_at"]), _utc(forecast["forecast_horizon_at"])
    except ValueError as e:
        raise ValueError(f"issued_at / forecast_horizon_at: {e}") from e
    if issued is None or horizon is None:
        raise ValueError("issued_at and forecast_horizon_at are required timestamps")
    if horizon < issued:
        raise ValueError("forecast_horizon_at is before issued_at")
    estimates = forecast["estimates"]
    if not isinstance(estimates, dict):
        raise ValueError("estimates must be a dict keyed by asset_id")
    for asset_id, est in estimates.items():
        if not isinstance(asset_id, str) or not asset_id:
            raise ValueError(f"estimate key {asset_id!r} is not an asset_id")
        if not isinstance(est, dict):
            raise ValueError(f"estimate {asset_id}: not a dict")
        extra = set(est) - set(ESTIMATE_KEYS)
        missing = [k for k in ESTIMATE_KEYS if k not in est]
        if extra or missing:
            raise ValueError(f"estimate {asset_id}: keys must be exactly {ESTIMATE_KEYS} (missing {missing}, extra {sorted(extra)})")
        times = {}
        for k in ("arrival_p10_at", "arrival_p50_at"):
            try:
                times[k] = _utc(est[k])
            except ValueError as e:
                raise ValueError(f"estimate {asset_id}: {k}: {e}") from e
            if times[k] is not None and times[k] > horizon:
                raise ValueError(f"estimate {asset_id}: {k} is after forecast_horizon_at")
        p10, p50 = times["arrival_p10_at"], times["arrival_p50_at"]
        if p10 is not None and p50 is not None and p10 > p50:
            raise ValueError(f"estimate {asset_id}: arrival_p10_at is after arrival_p50_at")
        bp = est["burn_probability"]
        if bp is not None:
            if isinstance(bp, bool) or not isinstance(bp, (int, float)) or math.isnan(bp) or not 0.0 <= bp <= 1.0:
                raise ValueError(f"estimate {asset_id}: burn_probability must be in [0, 1] or null")


def load_forecast(path) -> dict:
    """Read and validate a forecast-input-1 JSON file; ValueError on a bad file."""
    path = Path(path)
    try:
        forecast = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: not JSON ({e})") from e
    try:
        validate_forecast(forecast)
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from e
    return forecast


# ---------------------------------------------------------------------------------------- attach
def _mark_unavailable(rec: dict) -> None:
    if FORECAST_UNAVAILABLE not in rec["review_reasons"]:
        rec["review_reasons"].append(FORECAST_UNAVAILABLE)
    rec["needs_review"] = True


def _method_note(forecast: dict, basis: str | None) -> str:
    selected = f"fire_arrival_at = arrival_{basis}_at" if basis else "no arrival within the horizon: fire_arrival_at null"
    label = SYNTHETIC_LABEL if forecast["input_mode"] == "synthetic" else "recorded provider response"
    return (f"per-location estimates from {forecast['forecast_source']} ({forecast['input_mode']} "
            f"{SCHEMA_VERSION} file, declared basis {forecast['basis']}); {selected}; {label}")


def attach_forecast(assets, forecast) -> None:
    """Fill the forecast fields of `assets` (a list of asset records) IN PLACE from `forecast`.

    Every asset is touched: a located asset whose `asset_id` is in `estimates` gets `burn_probability`,
    `arrival_p10_at`, `arrival_p50_at`, `forecast_horizon_at`, `forecast_source`, and `fire_arrival_at`
    = p10 (basis "p10") else p50 (basis "p50") else null, plus one `sources` entry listing exactly the
    fields set (`observed_at` = the file's `issued_at`). Any asset left without `fire_arrival_at`
    (no coordinates, not in `estimates`, or no arrival within the horizon) gets `forecast_unavailable`
    in `review_reasons`. Previously attached forecast fields on covered assets are overwritten;
    uncovered assets keep whatever forecast fields they had only if those are already null.
    """
    validate_forecast(forecast)
    issued = _iso(forecast["issued_at"])
    horizon = _iso(forecast["forecast_horizon_at"])
    estimates = forecast["estimates"]
    for rec in assets:
        located = rec.get("latitude") is not None and rec.get("longitude") is not None
        est = estimates.get(rec["asset_id"]) if located else None
        if est is None:
            _mark_unavailable(rec)
            continue
        p10, p50 = _iso(est["arrival_p10_at"]), _iso(est["arrival_p50_at"])
        bp = est["burn_probability"]
        if p10 is not None:
            arrival, basis = p10, "p10"
        elif p50 is not None:
            arrival, basis = p50, "p50"
        else:
            arrival, basis = None, None
        values = {
            "burn_probability": None if bp is None else round(float(bp), 3),
            "arrival_p10_at": p10,
            "arrival_p50_at": p50,
            "forecast_horizon_at": horizon,
            "forecast_source": forecast["forecast_source"],
            "fire_arrival_at": arrival,
            "fire_arrival_basis": basis,
        }
        rec.update(values)
        fields = [k for k in FORECAST_FIELDS if values[k] is not None]
        rec.setdefault("sources", []).append({
            "fields": fields, "source": forecast["forecast_source"], "observed_at": issued,
            "available_at": None, "fetched_at": None, "notes": _method_note(forecast, basis)})
        if arrival is None:
            _mark_unavailable(rec)


# ------------------------------------------------------------------------------- extension point
def deepfire_spread_to_forecast(body: dict, received_at, *, asset_points: dict, horizon_at=None,
                                forecast_source: str = "deepfire:fire-spread") -> dict:
    """EXTENSION POINT (not implemented): Deepfire fire-spread response -> forecast-input-1 dict.

    Intended contract, to be filled in once the fire-spread endpoint's response shape is recorded
    (`fixtures/fire/deepfire/README.md` tracks what has been verified):

    - `body`: the raw fire-spread response (cached verbatim by `fire_input.record_response`).
    - `received_at`: when the response arrived (UTC); becomes `issued_at` unless the body carries an
      issue time, which is then preferred and noted.
    - `asset_points`: `{asset_id: (longitude, latitude)}` for the located assets to sample. The
      aggregation from the provider's grid/isochrones to one estimate per point (nearest cell,
      containing isochrone band, ...) must be documented in the returned `note`.
    - Returns a dict that passes `validate_forecast` with `input_mode: "recorded"`, `basis` set to
      what the provider actually supplies (`"p10"` only when it publishes quantiles), and one
      estimate per sampled asset; assets outside the provider's domain are left out so that
      `attach_forecast` marks them `forecast_unavailable`. No distance-based fallback.
    """
    raise NotImplementedError("Deepfire fire-spread adapter: response shape not yet recorded")
