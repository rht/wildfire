"""feeds.py tests: no network. requests.Session.get/post are monkeypatched, the cache lives in tmp_path."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import requests

from fireline import feeds

UTC = timezone.utc
T0 = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)


class FakeResponse:
    def __init__(self, body, status=200, headers=None):
        self._body, self.status_code, self.headers = body, status, headers or {}
        self.text = json.dumps(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(feeds, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.delenv("FIRELINE_OFFLINE", raising=False)
    monkeypatch.setattr(feeds.time, "sleep", lambda s: None)
    return tmp_path / "cache"


@pytest.fixture
def fake_get(monkeypatch):
    """Route Session.get to a handler(url, params) -> FakeResponse; records calls."""
    calls = []
    state = {"handler": lambda url, params: FakeResponse({})}

    def get(self, url, params=None, headers=None, timeout=None, **kw):
        calls.append((url, params))
        return state["handler"](url, params)

    monkeypatch.setattr(requests.Session, "get", get)
    state["calls"] = calls
    return state


# --------------------------------------------------------------------------- leak guard


def test_guard_raises_on_future_record():
    recs = [{"t": "2026-07-03T11:59:00Z"}, {"t": "2026-07-03T12:00:01Z"}]
    with pytest.raises(feeds.LeakError):
        feeds._guard(recs, "t", T0)


def test_guard_passes_records_at_or_before_as_of_and_geojson_properties():
    recs = [{"t": "2026-07-03T12:00:00Z"}, {"t": None},
            {"type": "Feature", "properties": {"t": "2026-07-01T00:00:00+02:00"}}]
    assert feeds._guard(recs, "t", T0) is recs


# --------------------------------------------------------------------------- cache


def test_cache_hit_works_offline(cache_dir, fake_get, monkeypatch):
    fake_get["handler"] = lambda url, params: FakeResponse({"hello": params["q"]})
    assert feeds.cached_get("https://x.test/a", {"q": 1}) == {"hello": 1}
    assert len(fake_get["calls"]) == 1
    # second call: served from cache, no network
    assert feeds.cached_get("https://x.test/a", {"q": 1}) == {"hello": 1}
    assert len(fake_get["calls"]) == 1
    monkeypatch.setenv("FIRELINE_OFFLINE", "1")
    fake_get["handler"] = lambda url, params: (_ for _ in ()).throw(AssertionError("network used offline"))
    assert feeds.cached_get("https://x.test/a", {"q": 1}, max_age_s=0) == {"hello": 1}
    with pytest.raises(feeds.FeedError):
        feeds.cached_get("https://x.test/a", {"q": 2})


def test_cache_expires_with_max_age(cache_dir, fake_get):
    n = {"v": 0}

    def handler(url, params):
        n["v"] += 1
        return FakeResponse({"v": n["v"]})

    fake_get["handler"] = handler
    assert feeds.cached_get("https://x.test/b", None)["v"] == 1
    assert feeds.cached_get("https://x.test/b", None, max_age_s=0)["v"] == 2


# --------------------------------------------------------------------------- Deepfire


def _feature(lon, lat, **props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props}


def test_hotspots_to_records_maps_fields():
    feats = [_feature(3.01, 41.9, observed_at="2026-07-03T11:30:00Z", fire_radiative_power=42.5,
                      source="MTG_I1", cluster_id="c1", confidence="HIGH")]
    rec = feeds.hotspots_to_records(feats)[0]
    assert rec["lon"] == 3.01 and rec["lat"] == 41.9
    assert rec["t"] == datetime(2026, 7, 3, 11, 30, tzinfo=UTC)
    assert rec["frp"] == 42.5 and rec["source"] == "MTG_I1" and rec["cluster_id"] == "c1"


def test_deepfire_hotspots_filters_guards_and_retries_busy(cache_dir, fake_get, monkeypatch):
    monkeypatch.setenv("DEEPFIRE_TOKEN", "tok")
    good = {"type": "FeatureCollection",
            "features": [_feature(3.0, 41.9, observed_at="2026-07-03T10:00:00Z", fire_radiative_power=5)]}
    seq = [FakeResponse({"code": "ogc-busy"}, 503, {"Retry-After": "0"}), FakeResponse(good)]
    fake_get["handler"] = lambda url, params: seq.pop(0)
    client = feeds.DeepfireClient()
    feats = client.hotspots(feeds.GAVARRES_BBOX, T0, since=T0.replace(hour=0))
    assert len(feats) == 1
    url, params = fake_get["calls"][0]
    assert url.endswith("/collections/deepfire:hotspots/items")
    assert params["filter-lang"] == "cql2-text"
    assert "observed_at <= TIMESTAMP('2026-07-03T12:00:00Z')" in params["filter"]
    assert "observed_at >= TIMESTAMP('2026-07-03T00:00:00Z')" in params["filter"]
    assert params["bbox"].startswith("2.850000,41.800000")
    # a leaked future record raises, even if the server returned it
    leak = {"type": "FeatureCollection", "features": [_feature(3.0, 41.9, observed_at="2026-07-03T12:00:01Z")]}
    fake_get["handler"] = lambda url, params: FakeResponse(leak)
    with pytest.raises(feeds.LeakError):
        client.hotspots(feeds.GAVARRES_BBOX, T0)


def test_poll_simulation_waits_until_terminal(cache_dir, fake_get, monkeypatch):
    monkeypatch.setenv("DEEPFIRE_TOKEN", "tok")
    seq = [FakeResponse({"id": "s1", "status": "QUEUED"}, 200, {"Retry-After": "0"}),
           FakeResponse({"id": "s1", "status": "COMPLETED", "result": {"features": []}})]
    fake_get["handler"] = lambda url, params: seq.pop(0)
    out = feeds.DeepfireClient().poll_simulation("s1")
    assert out["status"] == "COMPLETED" and fake_get["calls"][0][0].endswith("/v1/fire-spread/simulations/s1")


def test_start_spread_simulation_posts_documented_body(cache_dir, monkeypatch):
    monkeypatch.setenv("DEEPFIRE_TOKEN", "tok")
    posted = {}

    def post(self, url, params=None, headers=None, json=None, timeout=None, **kw):
        posted.update(url=url, body=json, headers=headers)
        return FakeResponse({"id": "s9", "status": "QUEUED"}, 202)

    monkeypatch.setattr(requests.Session, "post", post)
    out = feeds.DeepfireClient().start_spread_simulation(cluster_id="abc", duration_hours=6, ensemble_members=3)
    assert out["id"] == "s9"
    assert posted["url"].endswith("/v1/fire-spread/simulations")
    assert posted["body"] == {"durationHours": 6, "model": "elmfire", "ensembleMembers": 3, "clusterId": "abc"}
    assert posted["headers"]["Authorization"] == "Bearer tok"


# --------------------------------------------------------------------------- registers


def test_registers_to_assets_maps_each_register():
    care = [{"registre": "S01", "nom": "Residència Sol", "capacitat": "48", "municipi": "Bisbal d'Empordà, la",
             "tipologia": "Servei de residència assistida per a gent gran de caràcter temporal o permanent",
             "adreca": "C. Major, 1", "cp": "17100"},
            {"registre": "S02", "nom": "Centre de dia X", "capacitat": "20", "municipi": "Girona",
             "tipologia": "Servei de centre de dia per a gent gran"}]
    camps = [{"n_mero_inscripci": "KG-000123", "r_tol": "Càmping Pins", "total_places": "600",
              "tipus_establiment": "Càmpings", "municipi": "Calonge i Sant Antoni", "nom_de_la_via": "Ctra. X"}]
    schools = [{"codi_centre": "17000001", "denominaci_completa": "Escola Gavarres", "curs": "2025/2026",
                "nom_municipi": "Cassà de la Selva", "coordenades_geo_x": "2.8741", "coordenades_geo_y": "41.8895"},
               {"codi_centre": "17000002", "denominaci_completa": "Esc. de Música Municipal", "curs": "2025/2026",
                "nom_municipi": "Cassà de la Selva", "coordenades_geo_x": "2.87", "coordenades_geo_y": "41.89"}]
    equip = [{"idequipament": "1", "nom": "Hospital de Palamós", "categoria": "Salut|Centres sanitaris|3. Hospitals|",
              "poblacio": "Palamós", "longitud": "3.13", "latitud": "41.85"},
             {"idequipament": "2", "nom": "CAP", "categoria": "Salut|Centres sanitaris|1. Centres d'atenció primària (CAP)|",
              "poblacio": "Palamós", "longitud": "3.13", "latitud": "41.85"}]
    assets, unlocated = feeds.registers_to_assets(equip, care, camps, schools,
                                                  {"17000001": {"pupils": 312, "curs": "2024/2025"}})
    by_id = {a["asset_id"]: a for a in assets + unlocated}
    assert set(by_id) == {"care_homes:S01", "campsites:KG-000123", "schools:17000001", "schools:17000002",
                          "equipaments:1"}
    care_home = by_id["care_homes:S01"]
    assert care_home["asset_class"] == "care_home" and care_home["occupancy"] == 48
    assert care_home["occupancy_source"] == "register" and care_home["lon"] is None
    assert care_home in unlocated and "C. Major, 1" in care_home["address"]
    campsite = by_id["campsites:KG-000123"]
    assert campsite["asset_class"] == "campsite" and campsite["occupancy"] == 600 and campsite["seasonal"]
    assert campsite in unlocated
    school = by_id["schools:17000001"]
    assert school["asset_class"] == "school" and (school["lon"], school["lat"]) == (2.8741, 41.8895)
    assert school in assets and school["municipality"] == "Cassà de la Selva"
    # the schools directory has no occupancy column: pupils come from the enrolment register
    assert school["occupancy"] == 312 and school["occupancy_source"] == "enrolment"
    assert school["occupancy_register"] == "schools_enrolment" and school["occupancy_period"] == "2024/2025"
    unmatched = by_id["schools:17000002"]
    assert unmatched["occupancy"] is None and unmatched["occupancy_source"] == "unknown"
    assert "occupancy_register" not in unmatched
    hospital = by_id["equipaments:1"]
    assert hospital["asset_class"] == "hospital" and hospital in assets
    for a in assets + unlocated:
        assert {"asset_id", "name", "asset_class", "lon", "lat", "municipality"} <= set(a)


def test_schools_enrolment_sums_per_centre_and_falls_back_a_year(cache_dir, fake_get):
    by_year = {"2025/2026": [{"codi_centre": "17000001", "pupils": "635"}],
               "2024/2025": [{"codi_centre": "17000001", "pupils": "600"},
                             {"codi_centre": "17000009", "pupils": "84"}]}

    def handler(url, params):
        year = params["$where"].split("'")[1]
        return FakeResponse(by_year[year] if params["$offset"] == 0 else [])

    fake_get["handler"] = handler
    out = feeds.schools_enrolment(comarques=["Gironès"])
    # the current year wins where it lists the centre; the previous one only fills what it misses
    assert out == {"17000001": {"pupils": 635, "curs": "2025/2026"},
                   "17000009": {"pupils": 84, "curs": "2024/2025"}}
    url, params = fake_get["calls"][0]
    assert url.endswith("xvme-26kg.json")
    assert params["$group"] == "codi_centre" and params["$order"] == "codi_centre"
    assert params["$select"] == "codi_centre, sum(matr_cules_total) as pupils"
    assert "nom_comarca in ('Gironès')" in params["$where"]


def test_schools_enrolment_accepts_a_single_year(cache_dir, fake_get):
    fake_get["handler"] = lambda url, params: FakeResponse(
        [{"codi_centre": "17000001", "pupils": "12"}] if params["$offset"] == 0 else [])
    assert feeds.schools_enrolment(curs="2019/2020") == {"17000001": {"pupils": 12, "curs": "2019/2020"}}
    assert len(fake_get["calls"]) == 1   # a single year is a single query


# --------------------------------------------------------------------------- Open-Meteo


def test_open_meteo_wind_parses_canned_response(cache_dir, fake_get):
    times = [f"2026-07-03T{h:02d}:00" for h in range(24)]
    canned = {"latitude": 42.0, "longitude": 3.0, "hourly": {
        "time": times,
        "wind_speed_10m_previous_day1": [1.0] * 12 + [7.24] + [1.0] * 11,
        "wind_direction_10m_previous_day1": [0] * 12 + [6] + [0] * 11,
        "wind_gusts_10m_previous_day1": [None] * 24,
        "temperature_2m_previous_day1": [20.0] * 12 + [35.1] + [20.0] * 11,
        "relative_humidity_2m_previous_day1": [50] * 12 + [23] + [50] * 11,
    }}
    fake_get["handler"] = lambda url, params: FakeResponse(canned)
    w = feeds.open_meteo_wind(41.95, 3.03, T0, replay=True)
    url, params = fake_get["calls"][0]
    assert url == feeds.OPEN_METEO_PREVIOUS and params["models"] == "ecmwf_ifs025"
    assert params["wind_speed_unit"] == "ms" and params["timezone"] == "UTC"
    assert "wind_speed_10m_previous_day1" in params["hourly"]
    assert w["wind_speed_mps"] == 7.24 and w["wind_dir_deg"] == 6 and w["gust_mps"] is None
    assert w["temp_c"] == 35.1 and w["rh"] == 23 and w["valid_time"] == T0

    live = {"latitude": 42.0, "longitude": 3.0, "hourly": {
        "time": times, "wind_speed_10m": [2.5] * 24, "wind_direction_10m": [180] * 24,
        "wind_gusts_10m": [5.0] * 24, "temperature_2m": [25.0] * 24, "relative_humidity_2m": [40] * 24}}
    fake_get["handler"] = lambda url, params: FakeResponse(live)
    w = feeds.open_meteo_wind(41.95, 3.03, T0.replace(minute=37))
    assert fake_get["calls"][-1][0] == feeds.OPEN_METEO_LIVE
    assert w == {"wind_dir_deg": 180, "wind_speed_mps": 2.5, "gust_mps": 5.0, "temp_c": 25.0, "rh": 40,
                 "valid_time": T0, "source": "open-meteo forecast"}


# --------------------------------------------------------------------------- ArcGIS


def test_bombers_actuacions_filters_vegetation_and_as_of(cache_dir, fake_get):
    ms = lambda dt: int(dt.timestamp() * 1000)  # noqa: E731
    body = {"features": [
        {"attributes": {"TAL_DESC_ALARMA1": "incendi vegetació", "TAL_DESC_ALARMA2": "Incendi vegetació forestal",
                        "ACT_DAT_INICI": ms(T0.replace(hour=9)), "MUNICIPI_SIG": "Cruïlles"},
         "geometry": {"x": 3.0, "y": 41.9}},
        {"attributes": {"TAL_DESC_ALARMA1": "incendi vegetació", "TAL_DESC_ALARMA2": "Incendi vegetació forestal",
                        "ACT_DAT_INICI": ms(T0.replace(hour=13)), "MUNICIPI_SIG": "Later"},
         "geometry": {"x": 3.0, "y": 41.9}},
        {"attributes": {"TAL_DESC_ALARMA1": "incendi urbà", "TAL_DESC_ALARMA2": "Habitatge",
                        "ACT_DAT_INICI": ms(T0.replace(hour=9)), "MUNICIPI_SIG": "Girona"},
         "geometry": {"x": 2.8, "y": 41.98}},
    ], "exceededTransferLimit": False}
    fake_get["handler"] = lambda url, params: FakeResponse(body)
    rows = feeds.bombers_actuacions(T0)
    assert [r["MUNICIPI_SIG"] for r in rows] == ["Cruïlles"]
    assert rows[0]["t"] == T0.replace(hour=9) and rows[0]["lon"] == 3.0
    assert fake_get["calls"][0][1]["outSR"] == 4326


def test_pla_alfa_maps_municipality_to_level(cache_dir, fake_get):
    pages = [FakeResponse({"features": [{"attributes": {"CODIMUNI": "170221", "PERIL_M": 3}}],
                           "exceededTransferLimit": True}),
             FakeResponse({"features": [{"attributes": {"CODIMUNI": "170792", "PERIL_M": 1}}],
                           "exceededTransferLimit": False})]
    fake_get["handler"] = lambda url, params: pages.pop(0)
    assert feeds.pla_alfa(T0) == {"170221": 3, "170792": 1}
    assert fake_get["calls"][1][1]["resultOffset"] == 1
