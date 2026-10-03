"""ebay's own search, per country: the region picks the site (de is
ebay.de, gb ebay.co.uk, anything unknown ebay.com). `sold` lists what sold
and for how much, the real market price.

ebay sits behind akamai's bot manager, which answered 403 to every request,
the front page included, after a few searches (2026-10-03): ebay then rests
an hour like any blocked engine. meanwhile `site:ebay.de` on startpage finds
listings, without the filters."""

import re

from selectolax.parser import HTMLParser

from .common import ParseError, amount, settle, text

NAME = "ebay"
HOME = "https://www.ebay.com/"

SITES = {
    "us": "ebay.com", "de": "ebay.de", "gb": "ebay.co.uk", "uk": "ebay.co.uk", "ie": "ebay.ie", "fr": "ebay.fr",
    "it": "ebay.it", "es": "ebay.es", "at": "ebay.at", "ch": "ebay.ch", "nl": "ebay.nl", "pl": "ebay.pl",
    "au": "ebay.com.au", "ca": "ebay.ca",
}
# ebay's own codes: _sop for the order, LH_ItemCondition for the condition
SORTS = {"relevance": "12", "price_asc": "15", "price_desc": "16", "newest": "10", "ending": "1"}
CONDITIONS = {"new": "1000", "used": "3000"}
PARAMS = {"price_min": float, "price_max": float, "sort": tuple(SORTS), "condition": tuple(CONDITIONS), "sold": bool}

_ITEM = re.compile(r"/itm/(\d+)")
# the "shop on ebay" cards at the top of a results page are placeholders
_PLACEHOLDER = "123456"
# ebay's own words for an empty search, english and german
_NO_RESULTS = ("no exact matches found", "keine exakten treffer")


def site(region):
    return f"https://www.{SITES.get(region, 'ebay.com')}/"


def query_params(query, params):
    out = {"_nkw": query, "_sop": SORTS[params.get("sort", "relevance")]}
    if "price_min" in params:
        out["_udlo"] = amount(params["price_min"])
    if "price_max" in params:
        out["_udhi"] = amount(params["price_max"])
    if "condition" in params:
        out["LH_ItemCondition"] = CONDITIONS[params["condition"]]
    if params.get("sold"):
        out.update(LH_Sold="1", LH_Complete="1")
    return out


def parse(html, base=HOME):
    hits = []
    for card in HTMLParser(html).css("li.s-card, li.s-item"):
        link = card.css_first("a[href*='/itm/']")
        item = _ITEM.search(link.attributes.get("href") or "") if link is not None else None
        if item is None or item.group(1) == _PLACEHOLDER:
            continue
        rows = [text(card.css_first(".s-card__subtitle, .s-item__subtitle"))]
        rows += [text(row) for row in card.css(".s-card__attribute-row, .s-item__details > *, .s-card__caption")]
        hits.append({"title": text(card.css_first(".s-card__title, .s-item__title")),
                     "url": f"{base}itm/{item.group(1)}",
                     "snippet": " · ".join(dict.fromkeys(r for r in rows if r))})
    if not hits:
        if any(marker in html.lower() for marker in _NO_RESULTS):
            return []
        if len(html) > 20000:
            raise ParseError("no li.s-card with an /itm/ link on a full page")
    return hits


async def search(http, query, lang, region, n, params=None):
    base = site(region)
    resp = await http.get(NAME, base + "sch/i.html", lang=lang, referer=base, params=query_params(query, params or {}),
                          check=False)
    return settle(resp, lambda html: parse(html, base), query)[:n]
