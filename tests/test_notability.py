"""notability.py tests: no network. Session.get is monkeypatched, the feeds cache lives in tmp_path."""

from __future__ import annotations

import json
from datetime import timezone

import pytest
import requests

from fireline import feeds, notability

UTC = timezone.utc


class FakeResponse:
    """A JSON body, or a plain string for the text/plain bodies Wikimedia sends on a 429."""

    def __init__(self, body, status=200, headers=None):
        self._body, self.status_code, self.headers = body, status, headers or {}
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        if isinstance(self._body, str):
            raise ValueError("not JSON")
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(feeds, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.delenv("FIRELINE_OFFLINE", raising=False)
    monkeypatch.setattr(notability.time, "sleep", lambda s: None)
    return tmp_path / "cache"


@pytest.fixture
def fake_get(monkeypatch):
    """Route Session.get to a handler(url, params) -> FakeResponse; records calls."""
    calls = []
    state = {"handler": lambda url, params: FakeResponse({})}

    def get(self, url, params=None, headers=None, timeout=None, **kw):
        calls.append((url, params, headers))
        return state["handler"](url, params)

    monkeypatch.setattr(requests.Session, "get", get)
    state["calls"] = calls
    return state


@pytest.fixture
def no_socket(monkeypatch):
    """Any HTTP call at all is a test failure."""
    def boom(self, *a, **kw):
        raise AssertionError("network used")

    monkeypatch.setattr(requests.Session, "get", boom)
    monkeypatch.setattr(requests.Session, "post", boom)


def _pages(*pages, redirects=()):
    """A formatversion=2 batched Wikipedia response: pages is a list, redirects map query -> title."""
    return {"query": {"pages": list(pages),
                      "redirects": [{"from": f, "to": t} for f, t in redirects]}}


def _page(title, extract, requested=None):
    """One article. `requested` is the query the API redirected from, which is how a real acronym
    lookup arrives at a spelled-out title: "IRTA" -> "Institut de Recerca i Tecnologia ...".
    """
    return _pages({"pageid": 123, "title": title, "extract": extract},
                  redirects=[(requested, title)] if requested else ())


def _missing(*titles):
    return _pages(*[{"title": t, "missing": True} for t in titles])


def _titles(params):
    return params["titles"].split("|")


MISSING_PAGE = _pages({"title": "Nothing", "missing": True})

# The real article an "IRTA" lookup lands on: an acronym's article spells the acronym out, which is
# what fireline.notability._acronym_is_confirmed requires.
IRTA_TITLE = "Institut de Recerca i Tecnologia Agroalimentàries"


def _entity(qid, lang, title, claims=None):
    return {"entities": {qid: {"id": qid, "sitelinks": {f"{lang}wiki": {"title": title}},
                               "claims": claims or {}}}}


def _item(prop_id):
    return {"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {
        "type": "wikibase-entityid", "value": {"id": prop_id}}}}


NO_ENTITY = {"entities": {"-1": {"missing": ""}}}


def _corpus(tmp_path, records):
    path = tmp_path / "notability.json"
    notability.write_corpus(records, path, fetched_at="2026-09-19T00:00:00Z")
    return path


def _record(query, title, **kw):
    rec = notability._blank_record(query)
    rec.update(title=title, lang="ca", url=notability.article_url("ca", title),
               summary=f"{title} summary.", fetched_at="2026-09-19T00:00:00Z")
    rec.update(kw)
    return rec


# --------------------------------------------------------------------------- text helpers


def test_truncate_summary_cuts_on_a_sentence_boundary():
    long = ("L'IRTA és un centre de recerca. " * 40).strip()
    out = notability.truncate_summary(long)
    assert len(out) <= notability.SUMMARY_MAX_CHARS
    assert out.endswith("recerca.")
    assert long.startswith(out)          # a prefix of the source, so it stays quotable verbatim


def test_truncate_summary_keeps_short_text_and_collapses_whitespace():
    assert notability.truncate_summary("Dues\n\nlínies.  Ja.") == "Dues línies. Ja."


def test_truncate_summary_falls_back_to_a_word_boundary():
    out = notability.truncate_summary("paraula " * 200)
    assert len(out) <= notability.SUMMARY_MAX_CHARS and out.endswith("…")


def test_fold_is_case_and_accent_insensitive():
    assert notability.fold("Institut Català  d'ONCOLOGIA") == "institut catala d'oncologia"


# --------------------------------------------------------------------------- lookup


def test_lookup_ranks_exact_then_prefix_then_token(tmp_path):
    path = _corpus(tmp_path, [
        _record("IRTA", "IRTA"),
        _record("Institut Català d'Oncologia", "Institut Català d'Oncologia"),
        _record("Institut Català de Recerca de l'Aigua", "Institut Català de Recerca de l'Aigua"),
    ])
    exact = notability.lookup("IRTA", path=path)
    assert exact[0]["title"] == "IRTA"
    prefix = notability.lookup("Institut Català", limit=5, path=path)
    # both prefix matches, ordered by folded title, and IRTA does not appear (no shared token)
    assert [r["title"] for r in prefix] == ["Institut Català d'Oncologia",
                                            "Institut Català de Recerca de l'Aigua"]


def test_lookup_is_accent_and_case_insensitive(tmp_path):
    path = _corpus(tmp_path, [_record("Institut Català d'Oncologia", "Institut Català d'Oncologia")])
    assert notability.lookup("institut catala d'oncologia", path=path)[0]["wikidata_id"] is None
    assert len(notability.lookup("INSTITUT CATALA D'ONCOLOGIA", path=path)) == 1


def test_lookup_matches_a_long_token_so_irta_monells_finds_irta(tmp_path):
    path = _corpus(tmp_path, [_record("IRTA", "IRTA"),
                              _record("Girona", "Girona")])
    hits = notability.lookup("IRTA Monells", path=path)
    assert [r["title"] for r in hits] == ["IRTA"]


def test_lookup_ignores_short_tokens(tmp_path):
    path = _corpus(tmp_path, [_record("Institut Català d'Oncologia", "Institut Català d'Oncologia")])
    assert notability.lookup("de la", path=path) == []


def test_lookup_honours_limit_and_is_deterministic(tmp_path):
    path = _corpus(tmp_path, [_record(f"Centre {n}", f"Centre de Recerca {n}") for n in "CABED"])
    first = notability.lookup("Centre de Recerca", limit=3, path=path)
    second = notability.lookup("Centre de Recerca", limit=3, path=path)
    assert [r["title"] for r in first] == [r["title"] for r in second]
    assert [r["title"] for r in first] == ["Centre de Recerca A", "Centre de Recerca B",
                                           "Centre de Recerca C"]


def test_lookup_on_a_missing_corpus_is_empty(tmp_path):
    assert notability.lookup("IRTA", path=tmp_path / "nope.json") == []


# --------------------------------------------------------------------------- fetch


def test_fetch_notability_record_shape(cache_dir, fake_get):
    long_extract = ("L'IRTA és un centre de recerca de la Generalitat de Catalunya. " * 20).strip()

    def handler(url, params):
        if "wikipedia" in url:
            return FakeResponse(_page(IRTA_TITLE, long_extract, requested="IRTA"))
        if params["action"] == "wbgetentities" and "titles" in params:
            return FakeResponse(_entity("Q3151006", "ca", IRTA_TITLE, {
                "P31": [_item("Q31855")],
                "P137": [_item("Q5705")],
                "P571": [{"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {
                    "type": "time", "value": {"time": "+1985-01-01T00:00:00Z"}}}}],
                "P1128": [{"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {
                    "type": "quantity", "value": {"amount": "+680"}}}}],
            }))
        return FakeResponse({"entities": {
            "Q31855": {"labels": {"ca": {"language": "ca", "value": "institut de recerca"}}},
            "Q5705": {"labels": {"en": {"language": "en", "value": "Government of Catalonia"}}}}})

    fake_get["handler"] = handler
    (rec,) = notability.fetch_notability(["IRTA"])
    assert set(rec) == {"query", "title", "lang", "url", "summary", "wikidata_id", "instance_of",
                        "operator", "inception", "employees", "fetched_at"}
    assert rec["query"] == "IRTA" and rec["title"] == IRTA_TITLE and rec["lang"] == "ca"
    assert rec["url"] == ("https://ca.wikipedia.org/wiki/"
                          "Institut_de_Recerca_i_Tecnologia_Agroaliment%C3%A0ries")
    assert len(rec["summary"]) <= 600 and rec["summary"].endswith("Catalunya.")
    assert rec["wikidata_id"] == "Q3151006"
    assert rec["instance_of"] == ["institut de recerca"]
    assert rec["operator"] == "Government of Catalonia"
    assert rec["inception"] == "1985" and rec["employees"] == 680
    assert rec["fetched_at"].endswith("Z")
    # Wikimedia etiquette: a descriptive User-Agent on every request
    assert all(h["User-Agent"] == notability.USER_AGENT for _, _, h in fake_get["calls"])


def test_fetch_notability_falls_through_langs_on_a_missing_page(cache_dir, fake_get):
    def handler(url, params):
        if url.startswith("https://ca."):
            return FakeResponse(_missing(*_titles(params)))
        if url.startswith("https://es."):
            return FakeResponse(_pages(
                {"title": "Centro de Supercomputación de Barcelona",
                 "extract": "El Barcelona Supercomputing Center alberga el MareNostrum."},
                redirects=[("Barcelona Supercomputing Center", "Centro de Supercomputación de Barcelona")]))
        if "wikipedia" in url:
            raise AssertionError("English was queried although Spanish answered")
        return FakeResponse(NO_ENTITY)

    fake_get["handler"] = handler
    (rec,) = notability.fetch_notability(["Barcelona Supercomputing Center"])
    assert rec["lang"] == "es" and rec["title"] == "Centro de Supercomputación de Barcelona"
    assert rec["wikidata_id"] is None and rec["instance_of"] == []
    assert rec["operator"] is None and rec["inception"] is None and rec["employees"] is None


def test_fetch_notability_returns_no_record_when_no_lang_has_the_page(cache_dir, fake_get):
    fake_get["handler"] = lambda url, params: FakeResponse(_missing(*_titles(params)))
    assert notability.fetch_notability(["Escola Joan de Margarit"]) == []
    # one Wikipedia batch per language, and no Wikidata lookup for a page that does not exist
    assert len(fake_get["calls"]) == len(notability.WIKIPEDIA_LANGS)


def test_fetch_notability_cached_only_opens_no_socket(cache_dir, fake_get, no_socket):
    # cached_only must not even reach requests: no_socket makes any call an error
    assert notability.fetch_notability(["IRTA", "Girona"], cached_only=True) == []


def test_fetch_notability_cached_only_serves_what_is_cached(cache_dir, fake_get, monkeypatch):
    fake_get["handler"] = lambda url, params: (
        FakeResponse(_page(IRTA_TITLE, "L'IRTA és un centre de recerca.", requested="IRTA"))
        if "wikipedia" in url else FakeResponse(NO_ENTITY))
    assert notability.fetch_notability(["IRTA"])[0]["title"] == IRTA_TITLE
    warm = notability.fetch_notability(["IRTA"], cached_only=True)

    def boom(self, *a, **kw):
        raise AssertionError("network used")

    monkeypatch.setattr(requests.Session, "get", boom)
    assert notability.fetch_notability(["IRTA"], cached_only=True) == warm
    assert warm[0]["summary"] == "L'IRTA és un centre de recerca."


def test_fetch_notability_labels_are_batched(cache_dir, fake_get):
    ids = [f"Q{n}" for n in range(1, 61)]

    def handler(url, params):
        if "wikipedia" in url:
            return FakeResponse(_page("Institut Gran", "Institut Gran té molts tipus."))
        if params["action"] == "wbgetentities" and "titles" in params:
            return FakeResponse(_entity("Q1", "ca", "Institut Gran", {"P31": [_item(i) for i in ids]}))
        return FakeResponse({"entities": {
            i: {"labels": {"ca": {"language": "ca", "value": f"centre de recerca {i}"}}}
            for i in params["ids"].split("|")}})

    fake_get["handler"] = handler
    (rec,) = notability.fetch_notability(["Institut Gran"])
    assert len(rec["instance_of"]) == 60 and rec["instance_of"][0].startswith("centre de recerca")
    label_calls = [p for u, p, _ in fake_get["calls"] if "ids" in (p or {})]
    assert [len(p["ids"].split("|")) for p in label_calls] == [50, 10]


def test_fetch_notability_skips_duplicate_queries(cache_dir, fake_get):
    fake_get["handler"] = lambda url, params: (
        FakeResponse(_page(IRTA_TITLE, "Centre. L'IRTA hi és.", requested="IRTA"))
        if "wikipedia" in url else FakeResponse(NO_ENTITY))
    assert len(notability.fetch_notability(["IRTA", "irta", " IRTA "])) == 1


# --------------------------------------------------------------------------- query selection


def test_is_generic_name_is_a_cost_filter_on_register_boilerplate():
    assert notability.is_generic_name("Escola Joan de Margarit")
    assert notability.is_generic_name("Càmping Gavarres")
    assert notability.is_generic_name("Residència Puig d'en Roca")
    assert not notability.is_generic_name("IRTA Monells")
    assert not notability.is_generic_name("Institut Català d'Oncologia")


def test_derive_queries_expands_parentheses_and_leading_acronyms():
    assert notability.derive_queries("IRTA Monells (IRTA-Monells)") == [
        "IRTA Monells (IRTA-Monells)", "IRTA Monells", "IRTA-Monells", "IRTA"]
    assert notability.derive_queries("Parc de Bombers de la Pera") == ["Parc de Bombers de la Pera"]
    assert notability.derive_queries("") == []


def test_asset_queries_drops_generic_names_and_deduplicates():
    assets = [{"name": "IRTA Monells"}, {"name": "IRTA Monells"}, {"name": "Escola Joan de Margarit"},
              {"name": "Institut Català d'Oncologia"}]
    # sorted on the folded key, so the order is stable whatever the register hands over
    assert notability.asset_queries(assets) == ["Institut Català d'Oncologia", "IRTA", "IRTA Monells"]


# --------------------------------------------------------------------------- corpus file


def test_write_corpus_sorts_by_query_and_round_trips(tmp_path):
    path = _corpus(tmp_path, [_record("Zeta", "Zeta"), _record("Alfa", "Alfa")])
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == notability.CORPUS_VERSION
    assert payload["source"] == "wikipedia + wikidata"
    assert [r["query"] for r in payload["records"]] == ["Alfa", "Zeta"]
    before = path.read_bytes()
    notability.write_corpus(list(reversed(payload["records"])), path, fetched_at=payload["fetched_at"])
    assert path.read_bytes() == before      # byte-stable across runs


# --------------------------------------------------------------------------- rate limiting


def test_fetch_notability_retries_a_429_and_keeps_the_record(cache_dir, fake_get):
    """A throttled request recovers on the Retry-After; the article is still found."""
    seq = [FakeResponse("too many requests", 429, {"Retry-After": "0"}),
           FakeResponse(_page(IRTA_TITLE, "Centre de recerca de l'IRTA.", requested="IRTA"))]
    fake_get["handler"] = lambda url, params: (
        seq.pop(0) if seq and "wikipedia" in url
        else FakeResponse(NO_ENTITY))
    assert notability.fetch_notability(["IRTA"])[0]["title"] == IRTA_TITLE


def test_fetch_notability_stops_rather_than_record_throttling_as_absence(cache_dir, fake_get):
    """A rate-limited run must not silently claim every institution is unremarkable."""
    fake_get["handler"] = lambda url, params: FakeResponse("too many requests", 429,
                                                           {"Retry-After": "0"})
    with pytest.raises(feeds.FeedError) as exc:
        notability.fetch_notability([f"Centre {n}" for n in range(20)])
    assert "consecutive failed batches" in str(exc.value)


def test_fetch_notability_keeps_the_article_when_only_wikidata_fails(cache_dir, fake_get):
    def handler(url, params):
        if "wikipedia" in url:
            return FakeResponse(_page(IRTA_TITLE, "Centre de recerca de l'IRTA.", requested="IRTA"))
        return FakeResponse("too many requests", 429, {"Retry-After": "0"})

    fake_get["handler"] = handler
    (rec,) = notability.fetch_notability(["IRTA"])
    assert rec["title"] == IRTA_TITLE and rec["wikidata_id"] is None and rec["instance_of"] == []


# --------------------------------------------------------------------------- batching


def test_fetch_notability_batches_titles_into_one_request(cache_dir, fake_get):
    """Wikimedia throttles a per-IP bucket after ~10 requests, so titles go 20 to a call."""
    names = [f"Centre {n}" for n in range(45)]

    def handler(url, params):
        if "wikipedia" in url:
            return FakeResponse(_pages(*[{"title": t, "extract": f"{t} és un centre."}
                                         for t in _titles(params)]))
        return FakeResponse(NO_ENTITY)

    fake_get["handler"] = handler
    records = notability.fetch_notability(names, langs=("ca",))
    assert len(records) == 45
    wiki_calls = [p for u, p, _ in fake_get["calls"] if "wikipedia" in u]
    assert [len(_titles(p)) for p in wiki_calls] == [20, 20, 5]
    assert all(p["exlimit"] == 20 and p["redirects"] == 1 for p in wiki_calls)
    # one language answered everything, so the other two are never asked
    assert len(wiki_calls) == 3


def test_fetch_notability_follows_redirects_back_to_the_query(cache_dir, fake_get):
    def handler(url, params):
        if "wikipedia" in url:
            return FakeResponse(_pages(
                {"title": "Institut de Recerca i Tecnologia Agroalimentàries",
                 "extract": "L'Institut de Recerca i Tecnologia Agroalimentàries (IRTA) és un centre."},
                redirects=[("IRTA", "Institut de Recerca i Tecnologia Agroalimentàries")]))
        return FakeResponse(NO_ENTITY)

    fake_get["handler"] = handler
    (rec,) = notability.fetch_notability(["IRTA"], langs=("ca",))
    assert rec["query"] == "IRTA"
    assert rec["title"] == IRTA_TITLE
    assert rec["url"].endswith("Institut_de_Recerca_i_Tecnologia_Agroaliment%C3%A0ries")


def test_wikidata_is_resolved_through_the_sitelink(cache_dir, fake_get):
    def handler(url, params):
        if "wikipedia" in url:
            return FakeResponse(_page("Universitat de Girona", "La UdG és una universitat."))
        if "sites" in params:
            assert params["sites"] == "cawiki" and params["titles"] == "Universitat de Girona"
            return FakeResponse(_entity("Q1814503", "ca", "Universitat de Girona",
                                        {"P31": [_item("Q875538")]}))
        return FakeResponse({"entities": {"Q875538": {
            "labels": {"ca": {"language": "ca", "value": "universitat pública"}}}}})

    fake_get["handler"] = handler
    (rec,) = notability.fetch_notability(["Universitat de Girona"], langs=("ca",))
    assert rec["wikidata_id"] == "Q1814503"
    assert rec["instance_of"] == ["universitat pública"]


# --------------------------------------------------------------------------- acronym grounding


def test_an_acronym_is_kept_only_when_the_article_spells_it(cache_dir, fake_get):
    """"AH" is a Gencat service code; the Hijri-calendar article it lands on is not evidence."""
    articles = {
        "IRTA": ("Institut de Recerca i Tecnologia Agroalimentàries",
                 "L'Institut de Recerca i Tecnologia Agroalimentàries (IRTA) és un centre."),
        "AH": ("Año de la Hégira", "El año de la Hégira es el calendario musulmán."),
        "OMEGA": ("Omega", "Omega és la darrera lletra de l'alfabet grec."),
    }

    def handler(url, params):
        if "wikipedia" in url:
            pages, redirects = [], []
            for q in _titles(params):
                title, extract = articles[q]
                pages.append({"title": title, "extract": extract})
                if title != q:
                    redirects.append((q, title))
            return FakeResponse(_pages(*pages, redirects=redirects))
        return FakeResponse(NO_ENTITY)

    fake_get["handler"] = handler
    records = notability.fetch_notability(["IRTA", "AH", "OMEGA"], langs=("ca",))
    assert [r["query"] for r in records] == ["IRTA"]


def test_is_acronym_recognises_short_single_word_queries():
    assert notability.is_acronym("IRTA") and notability.is_acronym("UdG")
    assert not notability.is_acronym("IRTA Monells")
    assert not notability.is_acronym("girona")          # no capital, not an acronym
    assert not notability.is_acronym("Supercomputing")  # too long to be one


def test_derive_queries_drops_register_codes_and_numbers():
    # the full name is still queried; only the bare code and the "(3)" are dropped
    assert notability.derive_queries("CFA Girona") == ["CFA Girona"]
    assert notability.derive_queries("AH Hospital Universitari Josep Trueta - AH") == [
        "AH Hospital Universitari Josep Trueta - AH"]
    assert notability.derive_queries("Llar-Habitatge el Vilar (3)") == [
        "Llar-Habitatge el Vilar (3)", "Llar-Habitatge el Vilar"]
    assert notability.derive_queries("PLAYA BRAVA") == ["PLAYA BRAVA"]
    # a real institution acronym still survives
    assert "UdG" in notability.derive_queries("Facultat de Medicina (UdG)")


# --------------------------------------------------------------------------- relevance guards


def _wiki_and_wikidata(articles, claims=None, labels=None):
    """Handler serving `articles` {query: (title, extract)} plus optional P31 claims per title."""
    claims, labels = claims or {}, labels or {}

    def handler(url, params):
        if "wikipedia" in url:
            pages, redirects = [], []
            for q in _titles(params):
                title, extract = articles[q]
                pages.append({"title": title, "extract": extract})
                if title != q:
                    redirects.append((q, title))
            return FakeResponse(_pages(*pages, redirects=redirects))
        if "sites" in params:
            entities = {}
            for i, title in enumerate(params["titles"].split("|")):
                if title in claims:
                    entities[f"Q{i + 1}"] = {
                        "id": f"Q{i + 1}", "sitelinks": {"cawiki": {"title": title}},
                        "claims": {"P31": [_item(claims[title])]}}
            return FakeResponse({"entities": entities} if entities else NO_ENTITY)
        return FakeResponse({"entities": {
            i: {"labels": {"ca": {"language": "ca", "value": labels.get(i, i)}}}
            for i in params["ids"].split("|")}})

    return handler


def test_a_record_needs_a_title_that_shares_a_word_with_the_query(cache_dir, fake_get):
    """"Moby-Dick" the campsite and Moby Dick the novel are not the same subject."""
    fake_get["handler"] = _wiki_and_wikidata(
        {"Moby-Dick": ("Moby Dick", "Moby Dick és una novel·la de Herman Melville."),
         "Hospital de Palamós": ("Hospital de Palamós", "L'hospital de Palamós és un centre.")},
        claims={"Hospital de Palamós": "Q16917"}, labels={"Q16917": "hospital"})
    records = notability.fetch_notability(["Moby-Dick", "Hospital de Palamós"], langs=("ca",))
    assert [r["query"] for r in records] == ["Hospital de Palamós"]


def test_a_record_is_kept_only_when_wikidata_types_it_as_an_institution(cache_dir, fake_get):
    """A campsite trading as "Bambi" must not hand the agent the Disney film as evidence."""
    fake_get["handler"] = _wiki_and_wikidata(
        {"Bambi": ("Bambi", "Bambi és una pel·lícula d'animació de 1942."),
         "IRTA": (IRTA_TITLE, "L'IRTA és un centre de recerca.")},
        claims={"Bambi": "Q202866", IRTA_TITLE: "Q31855"},
        labels={"Q202866": "pel·lícula d'animació", "Q31855": "centre de recerca"})
    records = notability.fetch_notability(["Bambi", "IRTA"], langs=("ca",))
    assert [r["query"] for r in records] == ["IRTA"]
    assert records[0]["instance_of"] == ["centre de recerca"]


def test_an_unlabelled_claim_id_is_dropped_not_written_out(cache_dir, fake_get):
    """"Q31855" is an opaque token, not evidence an agent can quote."""
    def handler(url, params):
        if "wikipedia" in url:
            return FakeResponse(_page(IRTA_TITLE, "L'IRTA és un centre de recerca.", requested="IRTA"))
        if "sites" in params:
            return FakeResponse(_entity("Q1", "ca", IRTA_TITLE,
                                        {"P31": [_item("Q31855"), _item("Q43229")],
                                         "P137": [_item("Q5705")]}))
        return FakeResponse({"entities": {                 # only one of the three resolves
            "Q31855": {"labels": {"ca": {"language": "ca", "value": "centre de recerca"}}}}})

    fake_get["handler"] = handler
    (rec,) = notability.fetch_notability(["IRTA"], langs=("ca",))
    assert rec["instance_of"] == ["centre de recerca"]     # Q43229 dropped, not written raw
    assert rec["operator"] is None                         # unknown is null, never an id
    assert all(not str(v).startswith("Q") or not str(v)[1:].isdigit()
               for v in rec["instance_of"] + [rec["operator"]] if v)


def test_lookup_needs_one_name_to_contain_the_other(tmp_path):
    """A shared "Costa Brava" does not make an airport 30 km away a hit for a heliport."""
    path = _corpus(tmp_path, [
        _record("Aeroport de Girona-Costa Brava", "Aeroport de Girona - Costa Brava"),
        _record("IRTA", "IRTA"),
    ])
    assert notability.lookup("Heliport de Costa Brava Centre", path=path) == []
    assert [r["title"] for r in notability.lookup("IRTA Monells", path=path)] == ["IRTA"]
    # the airport is still found by its own name
    assert notability.lookup("Aeroport de Girona - Costa Brava", path=path)[0]["title"] == \
        "Aeroport de Girona - Costa Brava"


def test_lookup_still_finds_a_record_the_query_is_contained_in(tmp_path):
    path = _corpus(tmp_path, [_record("Institut Català d'Oncologia", "Institut Català d'Oncologia")])
    assert notability.lookup("Oncologia", path=path)[0]["title"] == "Institut Català d'Oncologia"


def test_asset_queries_skips_classes_whose_names_are_trade_names():
    assets = [{"name": "La Sirena", "asset_class": "campsite"},
              {"name": "Institut Jaume Vicens Vives", "asset_class": "school"},
              {"name": "IRTA Monells", "asset_class": "research_facility"}]
    assert notability.asset_queries(assets) == ["Institut Jaume Vicens Vives", "IRTA", "IRTA Monells"]


def test_an_acronym_whose_article_is_the_bare_acronym_is_dropped(cache_dir, fake_get):
    """"IRE" (Institut de Recerca Educativa) lands on the Institute of Radio Engineers, whose
    article is titled "IRE". The acronym matched itself, which confirms nothing."""
    fake_get["handler"] = _wiki_and_wikidata(
        {"IRE": ("IRE", "L'IRE (acrònim d'Institute of Radio Engineers) va ser una organització.")},
        claims={"IRE": "Q43229"}, labels={"Q43229": "organització"})
    assert notability.fetch_notability(["IRE"], langs=("ca",)) == []


def test_an_acronym_that_spells_out_is_kept(cache_dir, fake_get):
    fake_get["handler"] = _wiki_and_wikidata(
        {"IRTA": (IRTA_TITLE, "L'IRTA és un centre de recerca.")},
        claims={IRTA_TITLE: "Q31855"}, labels={"Q31855": "centre de recerca"})
    (rec,) = notability.fetch_notability(["IRTA"], langs=("ca",))
    assert rec["query"] == "IRTA" and rec["title"] == IRTA_TITLE
