"""Live and cached feeds with an ``as_of`` leak guard.

Every fetcher goes through :func:`cached_get`, a tiny JSON file cache in ``data/cache/`` keyed by
sha1 of (url, params). With ``FIRELINE_OFFLINE=1`` the cache is the only source, so the demo runs
on the last snapshot without network. Every function that returns time-stamped records takes
``as_of`` and passes the result through :func:`_guard`, which raises :class:`LeakError` if any
record is newer than ``as_of`` (CONTRACTS.md, Times).

Sources (PLAN.md section 5 and appendix, field names checked live on 2026-09-19, see
``data/README.md``):

* Deepfire OGC API Features (clusters, hotspots, satellite perimeters) and fire-spread simulations.
* Open-Meteo forecast API (live) and Previous Runs API (replay).
* Gencat Socrata registers: equipaments, care homes, tourism (campsites), schools.
* Bombers "actuacions urgents" and Pla Alfa ArcGIS FeatureServers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

from .env import load_env

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"

USER_AGENT = "Mozilla/5.0 (compatible; FireLine/0.1; hackbarna2026)"
DEFAULT_TIMEOUT_S = 60
DEFAULT_MAX_AGE_S = 3600
MAX_BUSY_RETRIES = 3

# Gavarres bbox, EPSG:4326 (CONTRACTS.md, Coordinates): (min_lon, min_lat, max_lon, max_lat).
GAVARRES_BBOX = (2.85, 41.80, 3.20, 42.05)

SOCRATA_BASE = "https://analisi.transparenciacatalunya.cat/resource/"
SOCRATA_DATASETS = {
    "equipaments": "8gmd-gz7i",
    "care_homes": "ivft-vegh",
    "campsites": "t2h3-cgys",
    "schools": "kvmv-ahh4",
}
SCHOOLS_CURS = "2025/2026"

OPEN_METEO_LIVE = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_PREVIOUS = "https://previous-runs-api.open-meteo.com/v1/forecast"
OPEN_METEO_VARS = [
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "temperature_2m",
    "relative_humidity_2m",
]
OPEN_METEO_REPLAY_MODEL = "ecmwf_ifs025"

ARCGIS = "https://services7.arcgis.com/ZCqVt1fRXwwK6GF4/arcgis/rest/services/"
# PLAN.md names ``ACTUACIONS_URGENTS_online_PRO_VW``, but that view only exposes
# ESRI_OID/GlobalID/EditDate. The sibling ``_PRO_AMB_FASE_VIEW`` carries the documented fields.
BOMBERS_URL = ARCGIS + "ACTUACIONS_URGENTS_online_PRO_AMB_FASE_VIEW/FeatureServer/0/query"
PLA_ALFA_URL = ARCGIS + "Pla_Alfa_Municipal_Avui_FL_2_view/FeatureServer/0/query"
VEGETATION_FIRE_KEYWORDS = ("vegetaci", "forestal")


class LeakError(Exception):
    """A feed would have returned a record newer than ``as_of``."""


class FeedError(Exception):
    """No data: network failure with no cached snapshot, or an API error."""


# --------------------------------------------------------------------------- time helpers


def to_utc(value: Any) -> datetime | None:
    """Parse ISO strings, epoch seconds/milliseconds or datetimes into aware UTC datetimes."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        secs = float(value)
        if secs > 1e11:  # epoch milliseconds (ArcGIS)
            secs /= 1000.0
        return datetime.fromtimestamp(secs, tz=timezone.utc)
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return to_utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record_ts(record: dict, ts_key: str) -> Any:
    """Timestamp of a record: top-level key, or GeoJSON ``properties[key]``."""
    if ts_key in record:
        return record[ts_key]
    props = record.get("properties")
    if isinstance(props, dict):
        return props.get(ts_key)
    return None


def _guard(records: list[dict], ts_key: str, as_of: datetime) -> list[dict]:
    """Return ``records`` unchanged, or raise :class:`LeakError` if any is newer than ``as_of``.

    Records with a missing/None timestamp pass (they cannot leak the future).
    """
    limit = to_utc(as_of)
    for rec in records:
        ts = to_utc(_record_ts(rec, ts_key))
        if ts is not None and ts > limit:
            raise LeakError(f"{ts_key}={ts.isoformat()} is newer than as_of={limit.isoformat()}")
    return records


# --------------------------------------------------------------------------- cache


_session: requests.Session | None = None


def get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = USER_AGENT
    return _session


def offline() -> bool:
    return os.environ.get("FIRELINE_OFFLINE", "").strip() not in ("", "0", "false", "no")


def _cache_key(url: str, params: dict | None) -> str:
    payload = json.dumps([url, params or {}], sort_keys=True, default=str)
    return hashlib.sha1(payload.encode()).hexdigest()


def _cache_path(url: str, params: dict | None) -> Path:
    return CACHE_DIR / f"{_cache_key(url, params)}.json"


def _read_cache(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_cache(path: Path, url: str, params: dict | None, body: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"url": url, "params": params, "fetched_at": _iso(datetime.now(timezone.utc)),
                   "body": body}, f, ensure_ascii=False)
    os.replace(tmp, path)


def _busy_retry_after(resp: requests.Response) -> float | None:
    """Seconds to wait if the response is a Deepfire ``ogc-busy`` 503 (or any 429/503 with Retry-After)."""
    if resp.status_code not in (429, 503):
        return None
    try:
        code = resp.json().get("code")
    except ValueError:
        code = None
    ra = resp.headers.get("Retry-After")
    if code == "ogc-busy" or ra is not None:
        try:
            return max(0.0, float(ra)) if ra is not None else 2.0
        except ValueError:
            return 2.0
    return None


def http(method: str, url: str, *, params: dict | None = None, headers: dict | None = None,
         json_body: Any = None, session: requests.Session | None = None,
         timeout: float = DEFAULT_TIMEOUT_S, retries: int = MAX_BUSY_RETRIES) -> requests.Response:
    """One HTTP call with the shared session; sleeps ``Retry-After`` on busy 503/429, at most ``retries`` times."""
    sess = session or get_session()
    for attempt in range(retries + 1):
        if method == "GET":
            resp = sess.get(url, params=params, headers=headers, timeout=timeout)
        else:
            resp = sess.post(url, params=params, headers=headers, json=json_body, timeout=timeout)
        wait = _busy_retry_after(resp)
        if wait is None or attempt == retries:
            return resp
        log.info("busy (%s), retry %d/%d after %.1fs: %s", resp.status_code, attempt + 1, retries, wait, url)
        time.sleep(wait)
    return resp  # pragma: no cover


def cached_get(url: str, params: dict | None = None, *, headers: dict | None = None,
               max_age_s: float | None = DEFAULT_MAX_AGE_S, session: requests.Session | None = None,
               timeout: float = DEFAULT_TIMEOUT_S) -> Any:
    """GET JSON through the file cache.

    * ``FIRELINE_OFFLINE=1``: return the cached body or raise :class:`FeedError`; never touch the network.
    * cached and younger than ``max_age_s`` (``None`` = never expires): return it.
    * otherwise fetch, store, return; on network failure fall back to a stale cache if one exists.
    """
    path = _cache_path(url, params)
    cached = _read_cache(path)
    if offline():
        if cached is None:
            raise FeedError(f"FIRELINE_OFFLINE=1 and no cache for {url} {params}")
        return cached["body"]
    if cached is not None:
        age = (datetime.now(timezone.utc) - to_utc(cached["fetched_at"])).total_seconds()
        if max_age_s is None or age <= max_age_s:
            return cached["body"]
    try:
        resp = http("GET", url, params=params, headers=headers, session=session, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
    except (requests.RequestException, ValueError) as exc:
        if cached is not None:
            log.warning("network failed (%s); using stale cache for %s", exc, url)
            return cached["body"]
        raise FeedError(f"{url}: {exc}") from exc
    _write_cache(path, url, params, body)
    return body


# --------------------------------------------------------------------------- Deepfire


class DeepfireClient:
    """Deepfire OGC API Features + fire-spread simulations (docs.deepfire.co).

    Time field per collection, verified against the docs on 2026-09-19. Change here if the live
    schema differs: clusters ``last_observed``; hotspots ``observed_at`` (PLAN.md's ``detected_at``
    does not exist); satellite perimeters ``computed_at`` (``observed_watermark`` is the acquisition
    time of the newest hotspot fed in and is nullable, so ``computed_at`` is the safer guard).
    """

    TIME_FIELD = {
        "clusters": "last_observed",
        "hotspots": "observed_at",
        "satellite-perimeters": "computed_at",
    }
    FRP_FIELD = "fire_radiative_power"
    ITEMS = "/ogc/features/v1/collections/deepfire:{collection}/items"
    PAGE_LIMIT = 10000  # server clamps here

    def __init__(self, token: str | None = None, base: str = "https://api.deepfire.co",
                 session: requests.Session | None = None, max_age_s: float | None = 60):
        self.base = base.rstrip("/")
        self.session = session or get_session()
        self.max_age_s = max_age_s
        load_env()  # .env credentials (never overrides the real environment)
        self._token = token or os.environ.get("DEEPFIRE_TOKEN") or None

    # -- auth
    def token(self) -> str:
        if self._token:
            return self._token
        cid, secret = os.environ.get("DEEPFIRE_CLIENT_ID"), os.environ.get("DEEPFIRE_CLIENT_SECRET")
        if not (cid and secret):
            raise FeedError("no Deepfire credentials: set DEEPFIRE_TOKEN or DEEPFIRE_CLIENT_ID/SECRET")
        resp = http("POST", self.base + "/v1/token", json_body={"client_id": cid, "client_secret": secret},
                    session=self.session)
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        return self._token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token()}", "Accept": "application/geo+json, application/json"}

    # -- OGC features
    def _items(self, collection: str, bbox: tuple, as_of: datetime, extra: list[str] | None = None) -> list[dict]:
        tf = self.TIME_FIELD[collection]
        clauses = [f"{tf} <= TIMESTAMP('{_iso(as_of)}')"] + list(extra or [])
        params = {
            "bbox": ",".join(f"{v:.6f}" for v in bbox),
            "filter-lang": "cql2-text",
            "filter": " AND ".join(clauses),
            "f": "application/geo+json",
            "limit": self.PAGE_LIMIT,
        }
        url = self.base + self.ITEMS.format(collection=collection)
        features: list[dict] = []
        for _ in range(100):  # follow OGC ``next`` links
            body = cached_get(url, params, headers=self._headers(), max_age_s=self.max_age_s, session=self.session)
            features.extend(body.get("features", []))
            nxt = next((l.get("href") for l in body.get("links", []) if l.get("rel") == "next"), None)
            if not nxt:
                break
            url, params = nxt, None
        return _guard(features, tf, as_of)

    def clusters(self, bbox: tuple, as_of: datetime, active: bool | None = None) -> list[dict]:
        extra = [f"active = {'true' if active else 'false'}"] if active is not None else []
        return self._items("clusters", bbox, as_of, extra)

    def hotspots(self, bbox: tuple, as_of: datetime, since: datetime | None = None) -> list[dict]:
        tf = self.TIME_FIELD["hotspots"]
        extra = [f"{tf} >= TIMESTAMP('{_iso(since)}')"] if since is not None else []
        return self._items("hotspots", bbox, as_of, extra)

    def satellite_perimeters(self, bbox: tuple, as_of: datetime) -> list[dict]:
        return self._items("satellite-perimeters", bbox, as_of)

    # -- fire spread
    def start_spread_simulation(self, cluster_id: str | None = None, lat: float | None = None,
                                lon: float | None = None, duration_hours: int = 12, model: str = "elmfire",
                                ensemble_members: int = 1, **extra) -> dict:
        """POST /v1/fire-spread/simulations; returns the response dict (``id``, ``status`` QUEUED...)."""
        body: dict[str, Any] = {"durationHours": int(duration_hours), "model": model,
                                "ensembleMembers": int(ensemble_members)}
        if cluster_id:
            body["clusterId"] = cluster_id
        elif lat is not None and lon is not None:
            body["latitude"], body["longitude"] = float(lat), float(lon)
        else:
            raise ValueError("cluster_id or lat/lon required")
        body.update(extra)  # e.g. sources=[...], lookbackHours=6
        resp = http("POST", self.base + "/v1/fire-spread/simulations", headers=self._headers(),
                    json_body=body, session=self.session)
        if resp.status_code >= 400:
            raise FeedError(f"fire-spread POST {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def poll_simulation(self, sim_id: str, timeout_s: float = 3600, default_wait_s: float = 10) -> dict:
        """GET /v1/fire-spread/simulations/{id} until status is COMPLETED, NO_SPREAD or FAILED."""
        url = f"{self.base}/v1/fire-spread/simulations/{sim_id}"
        deadline = time.monotonic() + timeout_s
        while True:
            resp = http("GET", url, headers=self._headers(), session=self.session)
            if resp.status_code >= 400:
                raise FeedError(f"simulation {sim_id}: {resp.status_code} {resp.text[:300]}")
            data = resp.json()
            if data.get("status") in ("COMPLETED", "NO_SPREAD", "FAILED"):
                return data
            if time.monotonic() > deadline:
                raise FeedError(f"simulation {sim_id} still {data.get('status')} after {timeout_s}s")
            try:
                wait = float(resp.headers.get("Retry-After", default_wait_s))
            except ValueError:
                wait = default_wait_s
            time.sleep(max(1.0, wait))


def hotspots_to_records(features: Iterable[dict]) -> list[dict]:
    """GeoJSON hotspot features -> CONTRACTS hotspot dicts ``{lon, lat, t, frp, source}``."""
    out = []
    for f in features:
        props = f.get("properties", {}) or {}
        coords = (f.get("geometry") or {}).get("coordinates") or [None, None]
        frp = props.get(DeepfireClient.FRP_FIELD, props.get("frp"))
        out.append({
            "lon": float(coords[0]) if coords[0] is not None else None,
            "lat": float(coords[1]) if coords[1] is not None else None,
            "t": to_utc(props.get(DeepfireClient.TIME_FIELD["hotspots"]) or props.get("detected_at")),
            "frp": float(frp) if frp is not None else None,
            "source": props.get("source"),
            "cluster_id": props.get("cluster_id"),
            "confidence": props.get("confidence"),
        })
    return out


# --------------------------------------------------------------------------- Open-Meteo


def open_meteo_series(lat: float, lon: float, start: date, end: date, replay: bool = False,
                      model: str = OPEN_METEO_REPLAY_MODEL, max_age_s: float | None = None) -> dict:
    """Hourly series for ``start..end`` (UTC dates). Returns ``{"time": [...], "wind_speed_10m": [...], ...}``.

    Live: forecast API; ``past_days``/``forecast_days`` cover the requested window (max 92 past days).
    Replay: Previous Runs API, ``models=<model>``, variables suffixed ``_previous_day1``: the value
    for each hour comes from the model run initialised about 24 h before the valid time, so nothing
    issued after ``valid_time - 24h`` leaks in. ``ecmwf_ifs025`` returns null gusts; ``best_match``
    has gusts but mixes models.
    """
    hourly = OPEN_METEO_VARS if not replay else [v + "_previous_day1" for v in OPEN_METEO_VARS]
    params: dict[str, Any] = {
        "latitude": round(float(lat), 4), "longitude": round(float(lon), 4),
        "hourly": ",".join(hourly), "wind_speed_unit": "ms", "timezone": "UTC",
        "start_date": start.isoformat(), "end_date": end.isoformat(),
    }
    if replay:
        params["models"] = model
        url, age = OPEN_METEO_PREVIOUS, (None if max_age_s is None else max_age_s)
    else:
        url, age = OPEN_METEO_LIVE, (600 if max_age_s is None else max_age_s)
    body = cached_get(url, params, max_age_s=age)
    if "hourly" not in body:
        raise FeedError(f"open-meteo: {body.get('reason', body)}")
    h = body["hourly"]
    out = {"time": h["time"], "latitude": body.get("latitude"), "longitude": body.get("longitude")}
    for v, key in zip(OPEN_METEO_VARS, hourly):
        out[v] = h.get(key)
    return out


def open_meteo_wind(lat: float, lon: float, as_of: datetime, replay: bool = False,
                    model: str = OPEN_METEO_REPLAY_MODEL) -> dict:
    """Wind and weather for the hour containing ``as_of`` (UTC).

    Returns ``{wind_dir_deg, wind_speed_mps, gust_mps, temp_c, rh, valid_time, source}``.
    Live mode uses the current forecast (fine when ``as_of`` is about now). Replay mode uses the
    Previous Runs API ``_previous_day1`` slice: the run initialised about 24 h before the valid time,
    which is the honest "what was known a day earlier" value and cannot leak observations after
    ``as_of``. Gusts are None when the model does not provide them (ecmwf_ifs025).
    """
    t = to_utc(as_of)
    hour = t.replace(minute=0, second=0, microsecond=0)
    day = hour.date()
    if replay:
        series = open_meteo_series(lat, lon, day, day, replay=True, model=model)
    else:
        series = open_meteo_series(lat, lon, day, day + timedelta(days=1), replay=False)
    stamp = hour.strftime("%Y-%m-%dT%H:%M")
    try:
        i = series["time"].index(stamp)
    except ValueError:
        raise FeedError(f"open-meteo: hour {stamp} not in response ({series['time'][:1]}..{series['time'][-1:]})")

    def pick(v):
        arr = series.get(v)
        return None if arr is None or arr[i] is None else float(arr[i])

    return {
        "wind_dir_deg": pick("wind_direction_10m"),
        "wind_speed_mps": pick("wind_speed_10m"),
        "gust_mps": pick("wind_gusts_10m"),
        "temp_c": pick("temperature_2m"),
        "rh": pick("relative_humidity_2m"),
        "valid_time": hour,
        "source": f"open-meteo previous_day1 {model}" if replay else "open-meteo forecast",
    }


# --------------------------------------------------------------------------- Gencat Socrata


def socrata(dataset_id: str, params: dict | None = None, as_of: datetime | None = None,
            ts_key: str | None = None, page: int = 5000, max_age_s: float | None = 7 * 86400) -> list[dict]:
    """All rows of a Socrata dataset, paging with ``$limit``/``$offset``.

    The registers are reference data without an event time, so ``as_of`` only guards when
    ``ts_key`` names a column (e.g. ``fasedatahora`` on plan activations).
    """
    url = f"{SOCRATA_BASE}{dataset_id}.json"
    base = dict(params or {})
    base.setdefault("$order", base.get("$order", ":id"))
    rows: list[dict] = []
    offset = 0
    while True:
        p = {**base, "$limit": page, "$offset": offset}
        chunk = cached_get(url, p, max_age_s=max_age_s)
        if not isinstance(chunk, list):
            raise FeedError(f"socrata {dataset_id}: {chunk}")
        rows.extend(chunk)
        if len(chunk) < page:
            break
        offset += page
    if as_of is not None and ts_key:
        _guard(rows, ts_key, as_of)
    return rows


def _in_list(values: Iterable[str]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _where(field: str, municipalities: Iterable[str] | None, comarca_field: str | None,
           comarques: Iterable[str] | None) -> list[str]:
    clauses = []
    if municipalities:
        clauses.append(f"{field} in ({_in_list(municipalities)})")
    if comarques and comarca_field:
        clauses.append(f"{comarca_field} in ({_in_list(comarques)})")
    return clauses


def _comarca_variants(comarques: Iterable[str]) -> list[str]:
    """Equipaments spells ``comarca`` two ways in one dataset: ``Baix Empordà`` (most rows) and
    ``BAIX EMPORDA`` (Salut rows, unaccented upper case). Match both."""
    out: list[str] = []
    for c in comarques:
        plain = "".join(ch for ch in unicodedata.normalize("NFD", c) if unicodedata.category(ch) != "Mn")
        for v in (c, plain.upper()):
            if v not in out:
                out.append(v)
    return out


def equipaments(bbox: tuple | None = None, comarques: Iterable[str] | None = None,
                categoria_like: Iterable[str] | None = None) -> list[dict]:
    """Gencat Equipaments (``8gmd-gz7i``). ``bbox`` is (min_lon, min_lat, max_lon, max_lat) on ``localitzacio``."""
    clauses = []
    if bbox:
        w, s, e, n = bbox
        clauses.append(f"within_box(localitzacio, {n}, {w}, {s}, {e})")
    clauses += _where("poblacio", None, "comarca", _comarca_variants(comarques) if comarques else None)
    if categoria_like:
        clauses.append("(" + " OR ".join(f"categoria like '%{k}%'" for k in categoria_like) + ")")
    params = {"$where": " AND ".join(clauses)} if clauses else {}
    return socrata(SOCRATA_DATASETS["equipaments"], params)


# Only residential social services are "care homes" for evacuation purposes; day centres, home help
# and office-type services are excluded server-side with these lower-case substrings on ``tipologia``.
CARE_HOME_TIPOLOGIA_KEYWORDS = ("residència", "residencial", "llar residència", "habitatge tutelat", "llar amb suport")


def care_homes(municipalities: Iterable[str] | None = None, comarques: Iterable[str] | None = None,
               residential_only: bool = True) -> list[dict]:
    """Registre d'entitats i serveis socials (``ivft-vegh``); no coordinates in this dataset."""
    clauses = _where("municipi", municipalities, "comarca", comarques)
    if residential_only:
        clauses.append("(" + " OR ".join(f"lower(tipologia) like '%{k}%'" for k in CARE_HOME_TIPOLOGIA_KEYWORDS) + ")")
    params = {"$where": " AND ".join(clauses)} if clauses else {}
    return socrata(SOCRATA_DATASETS["care_homes"], params)


def campsites(municipalities: Iterable[str] | None = None, comarques: Iterable[str] | None = None,
              tipus: str = "Càmpings", only_active: bool = True) -> list[dict]:
    """Registre de Turisme (``t2h3-cgys``) filtered to ``tipus_establiment``; no coordinates."""
    clauses = [f"tipus_establiment = '{tipus}'"] + _where("municipi", municipalities, "comarca", comarques)
    if only_active:
        clauses.append("estat = 'Alta'")
    return socrata(SOCRATA_DATASETS["campsites"], {"$where": " AND ".join(clauses)})


def schools(municipalities: Iterable[str] | None = None, comarques: Iterable[str] | None = None,
            curs: str = SCHOOLS_CURS) -> list[dict]:
    """Directori de centres docents (``kvmv-ahh4``), one school year; use ``coordenades_geo_x/y``."""
    clauses = [f"curs = '{curs}'"] + _where("nom_municipi", municipalities, "nom_comarca", comarques)
    return socrata(SOCRATA_DATASETS["schools"], {"$where": " AND ".join(clauses)})


# Ordered (substring, asset_class) rules per register, matched case-insensitively against the
# register's category column. First match wins; rows with no match are dropped (not assets).
# Extend here. ``class_ambiguous`` marks classes the exposure layer should flag for review.
ASSET_CLASS_RULES: dict[str, list[tuple[str, str]]] = {
    "equipaments": [  # column ``categoria`` (pipe hierarchy)
        ("3. hospitals", "hospital"),
        ("centres sociosanitaris", "care_home"),
        ("albergs de joventut", "camp"),
        ("cases de colònies", "camp"),
        ("càmping", "campsite"),
    ],
    "care_homes": [  # column ``tipologia``
        ("residència", "care_home"),
        ("residencial", "care_home"),
        ("habitatge tutelat", "care_home"),
        ("llar amb suport", "care_home"),
    ],
    "campsites": [  # column ``tipus_establiment``
        ("càmping", "campsite"),
        ("turisme rural", "masia"),
    ],
    "schools": [("", "school")],  # every row of the schools directory
}
CLASS_AMBIGUOUS = {("equipaments", "care_home"), ("equipaments", "camp"), ("campsites", "masia")}
REGISTER_CATEGORY_COLUMN = {"equipaments": "categoria", "care_homes": "tipologia",
                            "campsites": "tipus_establiment", "schools": "curs"}
REGISTER_ID_COLUMN = {"equipaments": "idequipament", "care_homes": "registre",
                      "campsites": "n_mero_inscripci", "schools": "codi_centre"}
REGISTER_NAME_COLUMN = {"equipaments": "nom", "care_homes": "nom", "campsites": "r_tol",
                        "schools": "denominaci_completa"}
REGISTER_MUNICIPALITY_COLUMN = {"equipaments": "poblacio", "care_homes": "municipi",
                                "campsites": "municipi", "schools": "nom_municipi"}
REGISTER_OCCUPANCY_COLUMN = {"care_homes": "capacitat", "campsites": "total_places"}


def classify(register: str, row: dict) -> str | None:
    text = str(row.get(REGISTER_CATEGORY_COLUMN[register], "") or "").lower()
    for needle, cls in ASSET_CLASS_RULES.get(register, []):
        if needle in text:
            return cls
    return None


def _float(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", ".")) if v not in (None, "") else None
    except ValueError:
        return None


def _int(v: Any) -> int | None:
    f = _float(v)
    return int(f) if f is not None else None


def _lonlat(row: dict) -> tuple[float | None, float | None]:
    lon = _float(row.get("longitud")) if "longitud" in row else _float(row.get("coordenades_geo_x"))
    lat = _float(row.get("latitud")) if "latitud" in row else _float(row.get("coordenades_geo_y"))
    if (lon is None or lat is None) and isinstance(row.get("localitzacio"), dict):
        c = row["localitzacio"].get("coordinates") or [None, None]
        lon, lat = _float(c[0]), _float(c[1])
    if lon is None or lat is None or not (-180 <= lon <= 180 and -90 <= lat <= 90):
        return None, None
    return lon, lat


def _address(row: dict) -> str:
    parts = [row.get("adreca"),
             " ".join(str(row.get(k)) for k in ("tipus_de_via", "nom_de_la_via", "numero") if row.get(k)),
             " ".join(str(row.get(k)) for k in ("tipus_via", "via", "num") if row.get(k)),
             row.get("adre_a"), row.get("cp") or row.get("codi_postal") or row.get("cpostal"),
             row.get("municipi") or row.get("poblacio") or row.get("nom_municipi")]
    return ", ".join(str(p).strip() for p in parts if p and str(p).strip())


def _asset_row(register: str, row: dict) -> dict | None:
    cls = classify(register, row)
    if cls is None:
        return None
    row_id = row.get(REGISTER_ID_COLUMN[register])
    occ_col = REGISTER_OCCUPANCY_COLUMN.get(register)
    occupancy = _int(row.get(occ_col)) if occ_col else None
    lon, lat = _lonlat(row)
    return {
        "asset_id": f"{register}:{row_id}",
        "name": row.get(REGISTER_NAME_COLUMN[register]) or "",
        "asset_class": cls,
        "lon": lon, "lat": lat,
        "municipality": row.get(REGISTER_MUNICIPALITY_COLUMN[register]) or "",
        "occupancy": occupancy,
        "occupancy_source": "register" if occupancy is not None else "unknown",
        "seasonal": cls == "campsite",
        "class_ambiguous": (register, cls) in CLASS_AMBIGUOUS,
        "register": register,
        "category": row.get(REGISTER_CATEGORY_COLUMN[register]),
        "address": _address(row),
    }


def registers_to_assets(equipaments: Iterable[dict] = (), care_homes: Iterable[dict] = (),
                        campsites: Iterable[dict] = (), schools: Iterable[dict] = ()) -> tuple[list[dict], list[dict]]:
    """Raw register rows -> (assets_in rows with coordinates, unlocated rows needing geocoding).

    Both lists hold CONTRACTS ``assets_in`` rows (``asset_id, name, asset_class, lon, lat, municipality``
    plus ``occupancy, occupancy_source, seasonal, class_ambiguous``); ``unlocated`` rows have
    ``lon``/``lat`` None and an ``address`` string for the geocoder.
    """
    assets, unlocated = [], []
    for register, rows in (("equipaments", equipaments), ("care_homes", care_homes),
                           ("campsites", campsites), ("schools", schools)):
        for row in rows:
            a = _asset_row(register, row)
            if a is None:
                continue
            (assets if a["lon"] is not None else unlocated).append(a)
    return assets, unlocated


# --------------------------------------------------------------------------- ArcGIS (Bombers, Pla Alfa)


def _arcgis_query(url: str, params: dict, max_age_s: float | None, page: int = 2000) -> list[dict]:
    """All features of a FeatureServer query, paging with ``resultOffset``."""
    feats: list[dict] = []
    offset = 0
    while True:
        p = {**params, "resultOffset": offset, "resultRecordCount": page}
        body = cached_get(url, p, max_age_s=max_age_s)
        if "error" in body:
            raise FeedError(f"arcgis {url}: {body['error']}")
        chunk = body.get("features", [])
        feats.extend(chunk)
        if not body.get("exceededTransferLimit") or not chunk:
            break
        offset += len(chunk)
    return feats


def bombers_actuacions(as_of: datetime, url: str = BOMBERS_URL, bbox: tuple | None = None,
                       max_age_s: float | None = 300) -> list[dict]:
    """Bombers urgent interventions that are vegetation fires started at or before ``as_of``.

    Returns dicts: all attributes plus ``lon``, ``lat``, ``t`` (``ACT_DAT_INICI`` as UTC datetime).
    The service is a live window (about the last days), so replay of July 2026 only works from a
    cached snapshot taken at the time.
    """
    feats = _arcgis_query(url, {"where": "1=1", "outFields": "*", "f": "json", "outSR": 4326}, max_age_s)
    out = []
    for f in feats:
        a = dict(f.get("attributes") or {})
        desc = " ".join(str(a.get(k) or "") for k in ("TAL_DESC_ALARMA1", "TAL_DESC_ALARMA2")).lower()
        if not any(k in desc for k in VEGETATION_FIRE_KEYWORDS):
            continue
        t = to_utc(a.get("ACT_DAT_INICI"))
        if t is None or t > to_utc(as_of):
            continue
        g = f.get("geometry") or {}
        lon, lat = g.get("x"), g.get("y")
        if bbox and not (lon is not None and bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
            continue
        a.update({"lon": lon, "lat": lat, "t": t})
        out.append(a)
    return _guard(out, "t", as_of)


def pla_alfa(as_of: datetime, url: str = PLA_ALFA_URL, max_age_s: float | None = 3600) -> dict[str, int]:
    """Today's Pla Alfa level per municipality: ``{CODIMUNI: PERIL_M}`` (0..4).

    The layer is "avui" only: it carries no date, so ``as_of`` cannot select a past day. In replay
    the value is whatever snapshot is cached; callers should treat it as context, not evidence.
    """
    feats = _arcgis_query(url, {"where": "1=1", "outFields": "CODIMUNI,NOMMUNI,PERIL_M",
                                "returnGeometry": "false", "f": "json", "outSR": 4326}, max_age_s)
    out: dict[str, int] = {}
    for f in feats:
        a = f.get("attributes") or {}
        if a.get("CODIMUNI") is not None and a.get("PERIL_M") is not None:
            out[str(a["CODIMUNI"])] = int(a["PERIL_M"])
    return out
