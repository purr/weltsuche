"""airbnb's stays search: the query is the place ("Berlin Prenzlauer Berg").

airbnb's pages are a javascript app, but each carries its data as json in
<script id="data-deferred-state-0">: a search page its results under
niobeClientData[...].data.presentation.staysSearch.results.searchResults, a
room page its facts under ...stayProductDetailPage.sections.metadata. both
are read from there; a room's description is in its json-ld (VacationRental). a listing's id hides in a base64 "DemandStayListing:<id>".

prices are totals for the stay when checkin and checkout are given, per night
otherwise; price_min and price_max filter by the price per night
(2026-10-03)."""

import base64
import binascii
import json
from datetime import date
from urllib.parse import quote, urlencode

from selectolax.parser import HTMLParser

from ..text import head_meta, visible_text
from .common import ParseError, amount, settle

NAME = "airbnb"
HOME = "https://www.airbnb.com/"

ROOM_TYPES = {"entire": "Entire home/apt", "private": "Private room", "shared": "Shared room", "hotel": "Hotel room"}
PARAMS = {"checkin": date, "checkout": date, "adults": int, "children": int, "price_min": float, "price_max": float,
          "room_type": tuple(ROOM_TYPES)}


def query_params(params):
    out = {k: params[k] for k in ("checkin", "checkout", "adults", "children") if k in params}
    out.update({k: amount(params[k]) for k in ("price_min", "price_max") if k in params})
    if "room_type" in params:
        out["room_types[]"] = ROOM_TYPES[params["room_type"]]
    return out


def state(html):
    """the page's data: the json of its first data-deferred-state script."""
    node = HTMLParser(html).css_first('script[id^="data-deferred-state"]')
    if node is None:
        return None
    try:
        return json.loads(node.text())
    except ValueError as e:
        raise ParseError(f"the data-deferred-state script is not json: {e}") from e


def _presentation(data):
    for entry in (data or {}).get("niobeClientData") or []:
        presentation = (((entry[1] if len(entry) > 1 else {}) or {}).get("data") or {}).get("presentation")
        if presentation:
            return presentation
    return {}


def listing_id(result):
    """the room id out of the base64 "DemandStayListing:<id>"."""
    raw = ((result.get("demandStayListing") or {}).get("id")) or ""
    try:
        return base64.b64decode(raw).decode().rpartition(":")[2]
    except (binascii.Error, UnicodeDecodeError):
        return ""


def parse(html, stay=""):
    """hits of a search page; `stay` is the dates and guests to put on each
    room link, so a fetch of it shows the same stay."""
    data = state(html)
    results = ((_presentation(data).get("staysSearch") or {}).get("results") or {}).get("searchResults")
    if results is None:
        if len(html) > 20000:
            raise ParseError("no staysSearch results in the page's data")
        return []
    hits = []
    for result in results:
        room = listing_id(result)
        if not room:
            continue
        price = (result.get("structuredDisplayPrice") or {}).get("primaryLine") or {}
        name = (((result.get("demandStayListing") or {}).get("description") or {}).get("name") or {})
        beds = [line.get("body") for line in ((result.get("structuredContent") or {}).get("mapPrimaryLine") or [])]
        facts = [price.get("accessibilityLabel") or " ".join(filter(None, (price.get("price") or price.get("discountedPrice"),
                                                                             price.get("qualifier")))),
                 f"rated {result['avgRatingLocalized']}" if result.get("avgRatingLocalized") else "",
                 ", ".join(b for b in beds if b),
                 ", ".join(b.get("text") for b in result.get("badges") or [] if b.get("text"))]
        title = " · ".join(filter(None, (result.get("title"), name.get("localizedStringWithTranslationPreference"))))
        hits.append({"title": title, "url": f"{HOME}rooms/{room}" + (f"?{stay}" if stay else ""),
                     "snippet": " · ".join(f for f in facts if f)})
    return hits


async def search(http, query, lang, region, n, params=None):
    params = params or {}
    stay = urlencode({k.replace("checkin", "check_in").replace("checkout", "check_out"): params[k]
                      for k in ("checkin", "checkout", "adults") if k in params})
    resp = await http.get(NAME, f"{HOME}s/{quote(query, safe='')}/homes", lang=lang, referer=HOME,
                          params=query_params(params), check=False)
    return settle(resp, lambda html: parse(html, stay), query)[:n]


def room_text(html):
    """(title, text) of a room page from its data: kind, capacity, ratings,
    superhost, and the description."""
    sections = (_presentation(state(html)).get("stayProductDetailPage") or {}).get("sections") or {}
    meta = sections.get("metadata") or {}
    share = meta.get("sharingConfig") or {}
    facts = ((meta.get("loggingContext") or {}).get("eventDataLogging")) or {}
    if not share:
        return "", ""
    ratings = ", ".join(f"{k.removesuffix('Rating')} {facts[k]}" for k in
                        ("accuracyRating", "cleanlinessRating", "checkinRating", "communicationRating",
                         "locationRating", "valueRating") if facts.get(k) is not None)
    lines = [share.get("title") or "",
             " · ".join(filter(None, (share.get("propertyType"), facts.get("roomType"),
                                      f"up to {share['personCapacity']} guests" if share.get("personCapacity") else "",
                                      "superhost" if facts.get("isSuperhost") else "",
                                      f"{facts['visibleReviewCount']} reviews" if facts.get("visibleReviewCount") else ""))),
             f"ratings: {ratings}" if ratings else ""]
    rental = _rental(html)
    address = rental.get("address") or {}
    lines.insert(1, ", ".join(filter(None, (rental.get("name"), address.get("addressLocality"),
                                            address.get("addressCountry")))))
    body = visible_text(rental.get("description") or "") or head_meta(html).get("description") or ""
    return share.get("title") or "", "\n".join(line for line in lines if line) + ("\n\n" + body if body else "")


def _rental(html):
    """the page's json-ld VacationRental: name, description, address."""
    for node in HTMLParser(html).css('script[type="application/ld+json"]'):
        try:
            found = json.loads(node.text())
        except ValueError:
            continue  # another script's broken json-ld says nothing about the rental
        if isinstance(found, dict) and found.get("@type") == "VacationRental":
            return found
    return {}
