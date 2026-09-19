"""Cached, quotable evidence about notable institutions (Wikipedia + Wikidata).

The criticality layer asks an LLM agent whether one particular building matters more than its class
average (a national research centre against an ordinary school). The agent may only propose what it
can quote verbatim from one of its own tool results (``scripts/validate.py`` ``_supported``) and may
only use numbers that appear in one (``fireline/agent.py`` ``postcheck_numbers``), so it needs an
offline corpus of quotable sentences. This module fetches that corpus and serves it.

Two halves, deliberately separated:

* :func:`fetch_notability` is the network side. Every request goes through :func:`feeds.cached_get`
  (``data/cache/<sha1>.json``), so a second run is offline-safe and ``cached_only=True`` never opens
  a socket.
* :func:`lookup` is the offline side used at agent runtime: a small fuzzy search over the committed
  ``fixtures/notability.json``. It never touches the network.

Sources, both free and keyless:

* Wikipedia ``action=query&prop=extracts`` (intro extract, plain text, redirects followed) on each
  language of :data:`WIKIPEDIA_LANGS` in turn. A page that does not exist comes back flagged
  ``missing`` with no extract, which is what "not notable" looks like.
* Wikidata ``action=wbgetentities`` keyed on ``sites``/``titles``, which resolves an article to its
  item through the sitelink exactly; ``action=wbsearchentities`` is the fallback for an article with
  no sitelink, and is accepted only on an exact label match. Claim item ids become labels through
  one further ``wbgetentities`` call, batched 50 ids at a time.

**Requests are batched, and that is not an optimisation.** Wikimedia gives an anonymous caller a
small per-IP token bucket: measured on 2026-09-19 from this machine, about ten requests drain it and
the edge then answers ``429 x-envoy-ratelimited`` with a ``Retry-After`` of 30-45 s. Title by title,
a 350-name corpus needs roughly a thousand requests and cannot be fetched at all. ``prop=extracts``
takes 20 titles per request (``exlimit``) and ``wbgetentities`` takes 50, which brings the same
corpus down to about fifty requests. Batching is also what the Wikimedia API etiquette asks for.
The consequence to know about: a cache entry covers a whole batch, so ``cached_only=True`` only
replays a run whose query list produces the same batches.

Record shape (CONTRACTS.md, Conventions: every key always present, unknown is ``null``, never zero)::

    {"query", "title", "lang", "url", "summary", "wikidata_id", "instance_of", "operator",
     "inception", "employees", "fetched_at"}
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import feeds

log = logging.getLogger(__name__)

ROOT = feeds.ROOT
CORPUS_PATH = ROOT / "fixtures" / "notability.json"
CORPUS_VERSION = "1.0"
CORPUS_SOURCE = "wikipedia + wikidata"

# Order of preference: the Catalan wiki carries the local institutions, Spanish and English fill in
# the rest. The first language with an intro extract wins and the later ones are never asked.
WIKIPEDIA_LANGS = ("ca", "es", "en")

WIKIPEDIA_API = "https://{lang}.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"

# Wikimedia's API etiquette asks for a descriptive User-Agent, serial requests and batching.
USER_AGENT = "FireLine/0.4 (hackbarna2026 wildfire; contact via repo)"
REQUEST_DELAY_S = 1.0
WIKIPEDIA_TITLES_PER_REQUEST = 20    # prop=extracts exlimit cap
WBGETENTITIES_MAX_IDS = 50           # ``ids=``/``titles=`` cap
# A throttled request must never be mistaken for "this institution has no article": after this many
# consecutive failed batches the fetch stops instead of writing absence it never observed.
MAX_CONSECUTIVE_ERRORS = 3

SUMMARY_MAX_CHARS = 600
MIN_TOKEN_LEN = 4                    # shorter tokens ("de", "la", "sant") match everything
ACRONYM_MAX_LEN = 6
# A one- or two-character short form ("3", "AH") is not an institution, it is a register code that
# happens to own an unrelated article, so it never becomes a query of its own.
MIN_SHORT_FORM_LEN = 3

# Wikidata properties read off the entity.
P_INSTANCE_OF = "P31"
P_OPERATOR = "P137"
P_PARENT_ORG = "P749"
P_INCEPTION = "P571"
P_EMPLOYEES = "P1128"

_SENTENCE_END = re.compile(r"[.!?][\"'’»)\]]*(?=\s|$)")
_WIKIDATA_TIME_YEAR = re.compile(r"^([+-])(\d{4})")


# --------------------------------------------------------------------------- text helpers


def fold(text: str) -> str:
    """Case- and accent-insensitive comparison key."""
    decomposed = unicodedata.normalize("NFD", str(text or ""))
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return " ".join(stripped.casefold().split())


def _tokens(text: str) -> set[str]:
    """Whitespace-separated tokens of at least :data:`MIN_TOKEN_LEN` characters, folded.

    A Catalan elision is carried along by the word it attaches to, so ``d'Oncologia`` yields both
    ``d'oncologia`` and ``oncologia`` and a search for either finds the record.
    """
    out = set()
    for raw in fold(text).split():
        tok = raw.strip(".,;:!?()[]{}'\"«»–—-·/")
        for candidate in (tok, tok.rpartition("'")[2]):
            if len(candidate) >= MIN_TOKEN_LEN:
                out.add(candidate)
    return out


def truncate_summary(text: str, limit: int = SUMMARY_MAX_CHARS) -> str:
    """Collapse whitespace and cut to ``limit`` characters on a sentence boundary.

    A quoted snippet has to match the corpus byte for byte, so the stored text is a single
    normalised line and always a prefix of the article's own intro. When no sentence ends inside the
    window the cut falls back to a word boundary with an ellipsis.
    """
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    window = clean[:limit]
    ends = [m.end() for m in _SENTENCE_END.finditer(window)]
    if ends:
        return window[:ends[-1]].rstrip()
    cut = window.rfind(" ")
    return (window[:cut] if cut > 0 else window).rstrip() + "…"


def article_url(lang: str, title: str) -> str:
    return f"https://{lang}.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"),
                                                                      safe=":/()',!$*-_.~")


def _chunks(seq: list, size: int) -> Iterable[list]:
    for start in range(0, len(seq), size):
        yield seq[start:start + size]


# --------------------------------------------------------------------------- cached HTTP


def _cache_entry(url: str, params: dict) -> dict | None:
    """The ``{"url", "params", "fetched_at", "body"}`` cache record for this call, or None.

    :func:`feeds.cached_get` does the fetching, but it answers with the body alone and decides for
    itself whether to open a socket. Peeking at its cache file first is what makes
    ``cached_only=True`` a guarantee rather than a hope, and it hands back the original
    ``fetched_at`` so a re-run from cache rewrites the corpus byte for byte.
    """
    return feeds._read_cache(feeds._cache_path(url, params))


def _api_get(url: str, params: dict, *, cached_only: bool = False,
             session: Any = None) -> tuple[Any, str | None]:
    """``(body, fetched_at)`` for one API call, or ``(None, None)`` when ``cached_only`` misses.

    A cached call never touches the network. An uncached one is fetched through
    :func:`feeds.cached_get` (the cache never expires: an encyclopedia article is reference data)
    and followed by :data:`REQUEST_DELAY_S` of politeness. A transport failure raises
    :class:`feeds.FeedError` rather than returning nothing: a 429 and a non-existent article look
    identical to a caller that only sees ``None``, and the whole point of the corpus is that a
    missing record means "ordinary", not "we were throttled".
    """
    cached = _cache_entry(url, params)
    if cached is not None:
        return cached.get("body"), cached.get("fetched_at")
    if cached_only:
        return None, None
    body = feeds.cached_get(url, params, headers={"User-Agent": USER_AGENT},
                            max_age_s=None, session=session)
    time.sleep(REQUEST_DELAY_S)
    entry = _cache_entry(url, params) or {}
    return body, entry.get("fetched_at")


# --------------------------------------------------------------------------- Wikipedia


def _wikipedia_params(titles: list[str]) -> dict:
    return {"action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1,
            "exlimit": WIKIPEDIA_TITLES_PER_REQUEST, "redirects": 1, "format": "json",
            "formatversion": 2, "titles": "|".join(titles)}


def _alias_map(block: dict, requested: list[str]) -> dict[str, str]:
    """Requested title -> final page title, following ``normalized`` then ``redirects`` hops."""
    hops: dict[str, str] = {}
    for key in ("normalized", "redirects"):
        for hop in block.get(key) or []:
            if hop.get("from") and hop.get("to"):
                hops[hop["from"]] = hop["to"]
    out = {}
    for title in requested:
        current, seen = title, set()
        while current in hops and current not in seen:
            seen.add(current)
            current = hops[current]
        out[title] = current
    return out


def _pages_by_title(block: dict) -> dict[str, dict]:
    pages = block.get("pages")
    if isinstance(pages, dict):                     # formatversion=1 shape, keyed by page id
        pages = list(pages.values())
    return {p["title"]: p for p in (pages or []) if isinstance(p, dict) and p.get("title")}


def wikipedia_extracts(queries: Iterable[str], lang: str, *, cached_only: bool = False,
                       session: Any = None) -> dict[str, dict]:
    """``{query: {"title", "extract", "fetched_at"}}`` for the queries that have an article.

    Up to :data:`WIKIPEDIA_TITLES_PER_REQUEST` titles per request. Queries whose page is missing or
    has an empty extract are simply absent from the result.
    """
    url = WIKIPEDIA_API.format(lang=lang)
    out: dict[str, dict] = {}
    for chunk in _chunks(list(queries), WIKIPEDIA_TITLES_PER_REQUEST):
        params = _wikipedia_params(chunk)
        body, fetched_at = _api_get(url, params, cached_only=cached_only, session=session)
        if not isinstance(body, dict):
            continue
        block = body.get("query") or {}
        alias = _alias_map(block, chunk)
        pages = _pages_by_title(block)
        for query in chunk:
            page = pages.get(alias.get(query, query)) or {}
            extract = (page.get("extract") or "").strip()
            if extract and page.get("title") and not page.get("missing"):
                out[query] = {"title": page["title"], "extract": extract, "fetched_at": fetched_at}
    return out


def wikipedia_extract(query: str, lang: str, *, cached_only: bool = False,
                      session: Any = None) -> dict | None:
    """One query's intro extract on ``lang``.wikipedia, redirects followed, or None."""
    return wikipedia_extracts([query], lang, cached_only=cached_only, session=session).get(query)


# --------------------------------------------------------------------------- Wikidata


def _entities_by_sitelink(lang: str, titles: list[str], *, cached_only: bool = False,
                          session: Any = None) -> dict[str, tuple[str, dict]]:
    """``{article title: (qid, entity)}``, resolved through the wiki sitelink, 50 titles per call."""
    site = f"{lang}wiki"
    out: dict[str, tuple[str, dict]] = {}
    for chunk in _chunks(list(titles), WBGETENTITIES_MAX_IDS):
        params = {"action": "wbgetentities", "sites": site, "titles": "|".join(chunk),
                  "props": "claims|labels|sitelinks", "sitefilter": site,
                  "languages": "|".join(WIKIPEDIA_LANGS), "format": "json"}
        body, _ = _api_get(WIKIDATA_API, params, cached_only=cached_only, session=session)
        if not isinstance(body, dict):
            continue
        for qid in sorted((body.get("entities") or {})):
            entity = body["entities"][qid] or {}
            if "missing" in entity or not str(entity.get("id") or "").startswith("Q"):
                continue
            title = ((entity.get("sitelinks") or {}).get(site) or {}).get("title")
            if title:
                out[title] = (entity["id"], entity)
    return out


def _entity_by_search(lang: str, title: str, query: str, *, cached_only: bool = False,
                      session: Any = None) -> tuple[str, dict] | None:
    """Fallback for an article with no sitelink: ``wbsearchentities``, exact label match only.

    A near miss here would attach another institution's staff count to this building and the agent
    would quote it as grounded evidence, so anything but an exact match leaves ``wikidata_id`` null.
    """
    params = {"action": "wbsearchentities", "search": title, "language": lang, "uselang": lang,
              "type": "item", "limit": 10, "format": "json"}
    body, _ = _api_get(WIKIDATA_API, params, cached_only=cached_only, session=session)
    if not isinstance(body, dict):
        return None
    wanted = {fold(title), fold(query)}
    qid = next((hit["id"] for hit in (body.get("search") or [])
                if hit.get("id") and {fold(hit.get("label") or ""),
                                      fold((hit.get("match") or {}).get("text") or "")} & wanted), None)
    if qid is None:
        return None
    params = {"action": "wbgetentities", "ids": qid, "props": "claims|labels",
              "languages": "|".join(WIKIPEDIA_LANGS), "format": "json"}
    body, _ = _api_get(WIKIDATA_API, params, cached_only=cached_only, session=session)
    entities = (body or {}).get("entities") or {} if isinstance(body, dict) else {}
    entity = entities.get(qid) or {}
    return (qid, entity) if entity and "missing" not in entity else None


def _statements(entity: dict, prop: str) -> list[dict]:
    """Non-deprecated statements for ``prop``, preferred ranks first, order otherwise preserved."""
    claims = (entity.get("claims") or {}).get(prop) or []
    kept = [c for c in claims if c.get("rank") != "deprecated"]
    return sorted(kept, key=lambda c: 0 if c.get("rank") == "preferred" else 1)


def _snak_value(statement: dict) -> Any:
    snak = statement.get("mainsnak") or {}
    if snak.get("snaktype") != "value":
        return None
    return (snak.get("datavalue") or {}).get("value")


def _item_ids(entity: dict, prop: str) -> list[str]:
    out = []
    for st in _statements(entity, prop):
        value = _snak_value(st)
        if isinstance(value, dict) and value.get("id"):
            out.append(value["id"])
    return out


def _year(entity: dict, prop: str = P_INCEPTION) -> str | None:
    for st in _statements(entity, prop):
        value = _snak_value(st)
        stamp = value.get("time") if isinstance(value, dict) else None
        m = _WIKIDATA_TIME_YEAR.match(str(stamp or ""))
        if m:
            return ("-" if m.group(1) == "-" else "") + str(int(m.group(2)))
    return None


def _quantity(entity: dict, prop: str = P_EMPLOYEES) -> int | None:
    for st in _statements(entity, prop):
        value = _snak_value(st)
        amount = value.get("amount") if isinstance(value, dict) else None
        try:
            return int(float(str(amount)))
        except (TypeError, ValueError):
            continue
    return None


def _labels(ids: Iterable[str], *, cached_only: bool = False, session: Any = None) -> dict[str, str]:
    """``{item id: label}`` in :data:`WIKIPEDIA_LANGS` preference order, batched 50 ids per call."""
    wanted = sorted({i for i in ids if i})
    out: dict[str, str] = {}
    for chunk in _chunks(wanted, WBGETENTITIES_MAX_IDS):
        params = {"action": "wbgetentities", "ids": "|".join(chunk), "props": "labels",
                  "languages": "|".join(WIKIPEDIA_LANGS), "format": "json"}
        body, _ = _api_get(WIKIDATA_API, params, cached_only=cached_only, session=session)
        if not isinstance(body, dict):
            continue
        for qid, entity in (body.get("entities") or {}).items():
            label = _pick_label((entity or {}).get("labels") or {})
            if label:
                out[qid] = label
    return out


def _pick_label(labels: dict) -> str | None:
    for lang in WIKIPEDIA_LANGS:
        value = (labels.get(lang) or {}).get("value")
        if value:
            return value
    for lang in sorted(labels):          # deterministic fallback, never dict order
        value = (labels.get(lang) or {}).get("value")
        if value:
            return value
    return None


# --------------------------------------------------------------------------- fetch


def _blank_record(query: str) -> dict:
    return {"query": query, "title": "", "lang": "", "url": "", "summary": "",
            "wikidata_id": None, "instance_of": [], "operator": None,
            "inception": None, "employees": None, "fetched_at": ""}


def is_acronym(query: str) -> bool:
    """True for a short single-word query such as ``IRTA``, ``UdG`` or ``CFA``."""
    q = str(query or "").strip()
    return bool(q) and " " not in q and 2 <= len(q) <= ACRONYM_MAX_LEN and any(c.isupper() for c in q)


# The corpus answers one question — "is this a notable *institution*?" — so a record is kept only
# when Wikidata types the article as one. Matched as substrings against the P31 labels, which come
# back in Catalan, Spanish or English. This is a precision filter: half the asset names in the
# register are trade names ("Bambi", "Neptuno", "Moby-Dick", "Sagrada Família") that resolve to a
# Disney film, a Roman god, a novel and a basilica, and any of those could then be quoted as
# evidence about a campsite. Extend the list when a real institution is wrongly dropped.
INSTITUTION_TYPE_KEYWORDS = (
    "institut", "institució", "institución", "institution", "centre", "centro", "center",
    "hospital", "clínica", "clinic", "universit", "facultat", "facultad", "faculty", "escola",
    "escuela", "school", "col·legi", "colegio", "college", "empresa", "company", "corporation",
    "organit", "organiz", "organis", "agència", "agencia", "agency", "fundació", "fundación",
    "foundation", "laborator", "museu", "museo", "museum", "biblioteca", "library", "bomber",
    "fire department", "fire service", "aeroport", "aeropuerto", "airport", "heliport",
    "helipuerto", "instal·lació", "instalación", "facility", "edifici", "edificio", "building",
    "campus", "parc científic", "administració", "administración", "servei públic", "public body",
    "consorci", "consorcio", "societat", "sociedad", "department", "departament",
)


def _is_institution(record: dict) -> bool:
    """Does Wikidata type this article as an institution, facility or building?

    A record with no Wikidata item at all is kept: there is nothing to judge it on, and an article
    that exists but carries no item is rare enough not to be worth guessing about.
    """
    if not record["wikidata_id"]:
        return True
    types = fold(" | ".join(record["instance_of"]))
    return any(k in types for k in (fold(x) for x in INSTITUTION_TYPE_KEYWORDS))


def _names_overlap(query: str, record: dict) -> bool:
    """Does the resolved article's title share a real word with the query that found it?

    "Moby-Dick" finding the novel, "Turismar" finding an unrelated company: a title that shares no
    token with the query is a different subject that merely sat on the same search string. Acronyms
    are exempt — "IRTA" shares nothing with "Institut de Recerca i Tecnologia Agroalimentàries" —
    because :func:`_acronym_is_confirmed` already holds them to a stricter test.
    """
    if is_acronym(query):
        return True
    return bool(_tokens(query) & _tokens(record["title"]))


def _acronym_is_confirmed(query: str, record: dict) -> bool:
    """Does the article itself spell this acronym?

    ``CFA``, ``LLI``, ``AH`` and friends are Gencat register codes, not institutions, and a bare
    three-letter query lands on whatever unrelated article happens to own it — "AH" on the Hijri
    calendar, "3" on the year 3. The article of a real acronym prints it, usually in the first
    sentence ("L'Institut de Recerca i Tecnologia Agroalimentàries (IRTA) …"), so requiring the
    acronym verbatim, in the spelling the register used, is the same grounding standard the agent
    itself is held to. Case matters: it is what separates IRTA from the Greek letter omega.
    """
    if query not in f"{record['title']} {record['summary']}":
        return False
    # ...and it must spell it *out*. An article whose own title is the bare acronym has not
    # confirmed anything: the acronym matched itself. "IRE" from "Institut de Recerca Educativa"
    # lands this way on the Institute of Radio Engineers, whose article is titled "IRE". A real
    # institution's article is titled with its name, not its initials. This does cost the
    # CERN/NASA shape, whose common name *is* the acronym; none appears in this corpus, and the
    # full register name remains a query of its own for those.
    return fold(record["title"]) != fold(query)


def fetch_notability(queries: Iterable[str], *, langs: Iterable[str] = WIKIPEDIA_LANGS,
                     cached_only: bool = False, session: Any = None) -> list[dict]:
    """One record per query that resolved to a Wikipedia article, in input order.

    Network, batched, through the :mod:`fireline.feeds` file cache. Queries that resolve nowhere (no
    article in any language, or with ``cached_only`` nothing cached) produce no record: absence is
    the signal that an asset is an ordinary one of its class. Three guards drop a resolved article
    that is not evidence about the asset: :func:`_acronym_is_confirmed`, :func:`_names_overlap` and
    :func:`_is_institution`.

    Raises :class:`feeds.FeedError` after :data:`MAX_CONSECUTIVE_ERRORS` consecutive failed batches,
    rather than recording throttled queries as absent. Everything already fetched stays cached, so a
    later re-run resumes.
    """
    wanted: list[str] = []
    seen: set[str] = set()
    for query in queries:
        query = str(query or "").strip()
        if query and fold(query) not in seen:
            seen.add(fold(query))
            wanted.append(query)

    found: dict[str, tuple[str, dict]] = {}     # query -> (lang, page)
    errors = 0
    for lang in langs:
        pending = [q for q in wanted if q not in found]
        if not pending:
            break
        for chunk in _chunks(pending, WIKIPEDIA_TITLES_PER_REQUEST):
            try:
                pages = wikipedia_extracts(chunk, lang, cached_only=cached_only, session=session)
            except feeds.FeedError as exc:
                errors += 1
                log.warning("notability: %s batch of %d failed: %s", lang, len(chunk), exc)
                if errors >= MAX_CONSECUTIVE_ERRORS:
                    raise feeds.FeedError(
                        f"{errors} consecutive failed batches, last {lang} {chunk[:1]}: {exc}. "
                        "Re-run later; everything already fetched is cached.") from exc
                continue
            errors = 0
            for query, page in pages.items():
                found[query] = (lang, page)

    records: list[dict] = []
    for query in wanted:
        if query not in found:
            continue
        lang, page = found[query]
        rec = _blank_record(query)
        rec.update(title=page["title"], lang=lang, url=article_url(lang, page["title"]),
                   summary=truncate_summary(page["extract"]), fetched_at=page["fetched_at"] or "")
        if is_acronym(query) and not _acronym_is_confirmed(query, rec):
            log.info("notability: dropping %r -> %r (the article does not spell the acronym)",
                     query, rec["title"])
            continue
        if not _names_overlap(query, rec):
            log.info("notability: dropping %r -> %r (title shares no word with the query)",
                     query, rec["title"])
            continue
        records.append(rec)

    _attach_wikidata(records, cached_only=cached_only, session=session)
    kept = []
    for rec in records:
        if _is_institution(rec):
            kept.append(rec)
        else:
            log.info("notability: dropping %r -> %r (%s is not an institution)",
                     rec["query"], rec["title"], rec["instance_of"] or "untyped")
    return kept


def _attach_wikidata(records: list[dict], *, cached_only: bool, session: Any) -> None:
    """Fill ``wikidata_id``/``instance_of``/``operator``/``inception``/``employees`` in place.

    Wikidata is a bonus: an article stands on its own as evidence, so a failure here is logged and
    the records keep their nulls rather than aborting a run that already has its summaries.
    """
    by_lang: dict[str, list[str]] = {}
    for rec in records:
        by_lang.setdefault(rec["lang"], []).append(rec["title"])
    entities: dict[tuple[str, str], tuple[str, dict]] = {}
    for lang in sorted(by_lang):
        try:
            resolved = _entities_by_sitelink(lang, sorted(set(by_lang[lang])),
                                             cached_only=cached_only, session=session)
        except feeds.FeedError as exc:
            log.warning("notability: wikidata sitelinks for %s unavailable: %s", lang, exc)
            continue
        for title, hit in resolved.items():
            entities[(lang, title)] = hit

    pending_ids: set[str] = set()
    for rec in records:
        hit = entities.get((rec["lang"], rec["title"]))
        if hit is None:
            try:            # no sitelink: the documented wbsearchentities fallback, exact match only
                hit = _entity_by_search(rec["lang"], rec["title"], rec["query"],
                                        cached_only=cached_only, session=session)
            except feeds.FeedError as exc:
                log.warning("notability: wikidata search for %r unavailable: %s", rec["query"], exc)
                hit = None
        if hit is None:
            continue
        qid, entity = hit
        operator_ids = _item_ids(entity, P_OPERATOR) or _item_ids(entity, P_PARENT_ORG)
        rec["wikidata_id"] = qid
        rec["instance_of"] = _item_ids(entity, P_INSTANCE_OF)
        rec["operator"] = operator_ids[0] if operator_ids else None
        rec["inception"] = _year(entity)
        rec["employees"] = _quantity(entity)
        pending_ids.update(rec["instance_of"])
        if rec["operator"]:
            pending_ids.add(rec["operator"])

    try:
        labels = _labels(pending_ids, cached_only=cached_only, session=session)
    except feeds.FeedError as exc:
        log.warning("notability: label batch unavailable: %s", exc)
        labels = {}
    # ids -> labels, one batched pass for the whole run. An id that did not resolve is dropped
    # rather than written out: "Q31855" is not evidence, it is an opaque token, and an agent asked
    # to justify a criticality tier from it can only either invent a meaning or (rightly) refuse.
    for rec in records:
        missing = [i for i in rec["instance_of"] if i not in labels]
        if missing:
            log.warning("notability %r: unlabelled instance_of %s dropped", rec["query"], missing)
        rec["instance_of"] = [labels[i] for i in rec["instance_of"] if i in labels]
        if rec["operator"]:
            rec["operator"] = labels.get(rec["operator"])


def write_corpus(records: Iterable[dict], path: Path | None = None, *,
                 fetched_at: str | None = None) -> Path:
    """Write ``fixtures/notability.json``, records sorted by query so the file is byte-stable."""
    path = Path(path) if path is not None else CORPUS_PATH
    ordered = sorted(records, key=lambda r: (fold(r["query"]), r["query"], r["title"]))
    payload = {"version": CORPUS_VERSION,
               "fetched_at": fetched_at or feeds._iso(datetime.now(timezone.utc)),
               "source": CORPUS_SOURCE, "records": ordered}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return path


# --------------------------------------------------------------------------- offline lookup


_CORPUS_CACHE: dict[tuple[str, int, int], list[dict]] = {}


def load_corpus(path: Path | None = None) -> list[dict]:
    """The committed corpus records. Cached on (path, mtime, size); no network, ever."""
    path = Path(path) if path is not None else CORPUS_PATH
    try:
        stat = path.stat()
    except OSError:
        return []
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    cached = _CORPUS_CACHE.get(key)
    if cached is None:
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError):
            return []
        cached = list(payload.get("records") or [])
        _CORPUS_CACHE.clear()
        _CORPUS_CACHE[key] = cached
    return cached


def _score(query: str, record: dict) -> tuple[int, int] | None:
    """``(rank, -token overlap)`` for a match, or None. Lower sorts first.

    Rank 0 exact (the query is the record's title or the query it was fetched under), 1 prefix,
    2 token: "IRTA Monells" shares the token "irta" with the "IRTA" record.
    """
    q = fold(query)
    if not q:
        return None
    names = [fold(record.get("title") or ""), fold(record.get("query") or "")]
    if q in names:
        return (0, 0)
    if any(name and (name.startswith(q) or q.startswith(name)) for name in names):
        return (1, 0)
    # A token match needs one name to *contain* the other, not merely to brush against it.
    # "Heliport de Costa Brava Centre" and "Aeroport de Girona - Costa Brava" share "Costa Brava"
    # and are 30 km apart; "IRTA Monells" and "IRTA" are the same institution. The difference is
    # that the record's whole name appears in the query, not just a word or two of it.
    asked = _tokens(query)
    for rank, name in ((2, "title"), (2, "query"), (3, None)):
        if name is not None:
            theirs = _tokens(record.get(name) or "")
            if theirs and theirs <= asked:
                return (rank, -len(theirs))
    theirs = _tokens(record.get("title") or "") | _tokens(record.get("query") or "")
    if theirs and asked and asked <= theirs:
        return (3, -len(asked))
    return None


def lookup(query: str, *, limit: int = 3, path: Path | None = None) -> list[dict]:
    """Offline search of the committed corpus. Best matches first, deterministic.

    Matching is case- and accent-insensitive on the record's ``query`` and ``title``, and on any
    whitespace-separated token of at least four characters, so "IRTA Monells" finds "IRTA". Ties
    break on the folded title and then the raw title, never on file order.
    """
    scored = []
    for record in load_corpus(path):
        score = _score(query, record)
        if score is not None:
            scored.append((score, fold(record.get("title") or ""), record.get("title") or "", record))
    scored.sort(key=lambda item: (item[0], item[1], item[2]))
    return [{**item[3], "instance_of": list(item[3].get("instance_of") or [])}
            for item in scored[:max(0, int(limit))]]


# --------------------------------------------------------------------------- query selection

# A cost filter, not a judgement. These are the names a register mints by the hundred — an ordinary
# primary school, a nursery, a campsite, a care home, a municipal police post — and essentially none
# of them has an encyclopedia article, so querying them spends requests to learn nothing. A site
# that is genuinely notable despite such a name is not excluded from the corpus: it can be named
# directly with the fetcher's ``--queries``. Matching is case- and accent-insensitive; a leading or
# trailing space in a pattern anchors it to a word edge.
GENERIC_FACILITY_PATTERNS = (
    "escola ", "ceip ", "institut escola", "escola bressol", "llar d'infants", "zer ",
    "col.legi ", "col·legi ", "ses ", "sies ",
    "camping ", "residencia ", "llar residencia", "habitatge tutelat", "casal ",
    "centre de dia", "centre residencial", "llar-residencia",
    "deixalleria", "policia local de", "guardia municipal de",
    "pades ", "cap ", "consultori local", "casa de colonies", "alberg ",
)

# Also a cost filter. These are Gencat register and hospital-service codes, not institution
# acronyms: CFA is "centre de formació d'adults", LLI a llar d'infants, AH an hospital d'aguts unit.
# The full name is still queried; only the bare code is dropped, because a two- or three-letter
# query lands on whatever unrelated article owns it and costs a request per language to find out.
REGISTER_CODE_PREFIXES = frozenset({
    "AFA", "AH", "CEE", "CFA", "EB", "EBM", "EOI", "ETG", "HDA", "LLI", "PADES", "SEETDIC",
    "SES", "SIEI", "UTCA", "URPIJ", "ZER",
})

# Also a cost filter, and for the same reason. A campsite, a holiday camp or a rural-tourism house
# is registered under its trade name ("Benelux", "King's", "La Sirena", "Turismar"), which resolves
# to an economic union, a London college, a frozen-food chain and a bus company. Nothing is lost:
# the criticality of these sites comes from how many people are on them, which the occupancy column
# already carries, not from institutional standing. Assets of any other class are still queried.
NON_INSTITUTIONAL_CLASSES = frozenset({"campsite", "camp", "masia"})

_TRAILING_PAREN = re.compile(r"\(([^()]{1,80})\)\s*$")
_LEADING_ACRONYM = re.compile(r"^([\w.]{2,10}?)(?=\s)", re.UNICODE)


def _fold_keep_spaces(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", str(text or ""))
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn").casefold()


def is_generic_name(name: str) -> bool:
    """True when the name matches :data:`GENERIC_FACILITY_PATTERNS` (skip it, it costs requests)."""
    padded = " " + " ".join(_fold_keep_spaces(name).split()) + " "
    return any(_fold_keep_spaces(p) in padded for p in GENERIC_FACILITY_PATTERNS)


def _useful_short_form(candidate: str, name: str) -> bool:
    """Is this derived short form worth a request of its own?"""
    if len(candidate) < MIN_SHORT_FORM_LEN or candidate.upper() in REGISTER_CODE_PREFIXES:
        return False
    if not any(c.isalpha() for c in candidate):     # "Llar-Habitatge el Vilar (3)" -> "3"
        return False
    # An all-capitals name ("PLAYA BRAVA") has no acronym, only a first word.
    return not (candidate == name.split()[0] and name == name.upper())


def derive_queries(name: str) -> list[str]:
    """Queries worth trying for one asset name, most specific first.

    The full name, the name without its trailing parenthesis, the text inside that parenthesis, and
    a leading acronym: ``"IRTA Monells (IRTA-Monells)"`` yields the name, ``"IRTA Monells"``,
    ``"IRTA-Monells"`` and ``"IRTA"``. Wikipedia carries the institution far more often than the
    individual site, so the short forms are the ones that usually resolve.
    """
    name = " ".join(str(name or "").split())
    if not name:
        return []
    out = [name]
    m = _TRAILING_PAREN.search(name)
    if m:
        stem = name[:m.start()].strip(" -–—,")
        inner = m.group(1).strip()
        if stem and stem not in out:
            out.append(stem)
        if inner and inner not in out and _useful_short_form(inner, name):
            out.append(inner)
    for head in list(out):
        m = _LEADING_ACRONYM.match(head)
        acronym = m.group(1).replace(".", "") if m else ""
        if (len(acronym) >= 2 and acronym.isalnum() and acronym.upper() == acronym
                and acronym not in out and _useful_short_form(acronym, name)):
            out.append(acronym)
    return out


def asset_queries(assets: Iterable[dict], *, name_key: str = "name",
                  class_key: str = "asset_class") -> list[str]:
    """Deduplicated, sorted queries for the asset rows worth checking.

    Rows of a :data:`NON_INSTITUTIONAL_CLASSES` class and rows whose name matches
    :data:`GENERIC_FACILITY_PATTERNS` are dropped before any request is spent on them.
    """
    seen: dict[str, str] = {}
    for asset in assets:
        asset = asset or {}
        name = asset.get(name_key) or ""
        if asset.get(class_key) in NON_INSTITUTIONAL_CLASSES or is_generic_name(name):
            continue
        for query in derive_queries(name):
            seen.setdefault(fold(query), query)
    return [seen[k] for k in sorted(seen)]
