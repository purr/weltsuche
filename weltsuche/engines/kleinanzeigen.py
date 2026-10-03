"""kleinanzeigen.de, germany's classifieds, through its results pages.

the filters live in the path: /s-{place}/{filters}/{query}/k0l{place id}r{km}
(/s-berlin/preis:200:400/thinkpad-t14/k0l3331r10). a place becomes its id
through the site's own suggestion endpoint (s-ort-empfehlungen.json; a
postcode or a town name). the price order is ascending only, and paid "top"
ads come first in every order (2026-10-03).

the page's class names are generated, so an ad is read through its
data-adid and data-href, the json-ld its picture carries (title and
description), and its visible lines: postcode and town, distance, date,
price, "Direkt kaufen" / "Versand möglich"."""

import json
import re
from urllib.parse import quote

from selectolax.parser import HTMLParser

from .common import ParseError, QueryError, amount, api_json, settle

NAME = "kleinanzeigen"
HOME = "https://www.kleinanzeigen.de/"
PARAMS = {"price_min": float, "price_max": float, "location": str, "radius_km": int, "sort": ("newest", "price_asc")}

_PLACE = re.compile(r"^\d{5} ")
_DISTANCE = re.compile(r"^\(.*km\)$")
_DATE = re.compile(r"^(heute|gestern|\d{2}\.\d{2}\.\d{4})", re.I)
# a whole price line ("180 €", "1.400 € VB", "VB", "Zu verschenken"): a
# pattern that only had to occur took "Neupreis 1.400 €, kein Tausch." from
# the description for the price (found in review 2026-10-03)
_PRICE = re.compile(r"^(?:\d[\d.]*(?:,\d+)?\s*€(?:\s*VB)?|VB|zu verschenken|tausch)$", re.I)
_NO_RESULTS = "keine ergebnisse"


def _slug(words):
    return quote("-".join(words.lower().split()), safe="")


def path(query, params, place=None):
    """the results path; `place` is (id, name) from place_id."""
    segments = [_slug(place[1])] if place else []
    if params.get("sort") == "price_asc":
        segments.append("sortierung:preis")
    if "price_min" in params or "price_max" in params:
        low, high = (amount(params[k]) if k in params else "" for k in ("price_min", "price_max"))
        segments.append(f"preis:{low}:{high}")
    tail = f"k0l{place[0]}" + (f"r{params['radius_km']}" if "radius_km" in params else "") if place else "k0"
    return "s-" + "/".join([*segments, _slug(query)]) + "/" + tail


async def place_id(http, place, query):
    """(id, name) of a postcode or town, as the site's place box finds it."""
    found = await api_json(http, NAME, HOME + "s-ort-empfehlungen.json", query, lang="de", params={"query": place})
    for key, name in found.items():
        if key != "_0":  # "_0" is all of germany
            return key.lstrip("_"), name
    raise QueryError(f"kleinanzeigen knows no place {place!r}; give a postcode or a town")


def parse(html):
    hits = []
    for ad in HTMLParser(html).css("article[data-adid]"):
        href = ad.attributes.get("data-href") or ""
        ld = ad.css_first('script[type="application/ld+json"]')
        try:
            about = json.loads(ld.text()) if ld is not None else {}
        except ValueError:
            about = {}  # a broken json-ld: title and lines come from the visible text
        for junk in ad.css("script, img, svg"):
            junk.decompose()
        # the ad's own elements: traverse() walks on into the next ads, and an
        # ad without a date took its neighbour's (found in review 2026-10-03)
        lines = [" ".join(node.text(deep=False).split()) for node in ad.css("*")]
        lines = [line for line in lines if line]
        place = next((line for line in lines if _PLACE.match(line)), "")
        facts = [next((line for line in lines if _PRICE.search(line)), ""), place,
                 next((line for line in lines if _DISTANCE.match(line)), ""),
                 next((line for line in lines if _DATE.match(line)), ""),
                 " ".join((about.get("description") or "").split())[:200]]
        title = about.get("title") or next((line for line in lines if line not in facts and not line.isdigit()), "")
        hits.append({"title": title, "url": HOME.rstrip("/") + href, "snippet": " · ".join(f for f in facts if f)})
    if not hits:
        if _NO_RESULTS in html.lower():
            return []
        if len(html) > 20000:
            raise ParseError("no article[data-adid] on a full page")
    return hits


async def search(http, query, lang, region, n, params=None):
    params = params or {}
    place = await place_id(http, params["location"], query) if params.get("location") else None
    if "radius_km" in params and place is None:
        raise QueryError("kleinanzeigen: radius_km needs a location")
    resp = await http.get(NAME, HOME + path(query, params, place), lang="de", referer=HOME, check=False)
    return settle(resp, parse, query)[:n]
