"""bing, html results. honours language and market.

not in the default engine sets: bing answers a client it does not trust with
a well-formed results page for some other query entirely (measured: "Framework
16 laptop review" came back as Code::Blocks downloads, then Firefox tips).
the decoy check in `engines.run` catches that, so nothing wrong gets cached,
but it means bing is only worth asking for explicitly to see if it has changed
its mind. duckduckgo serves the same index without the games.
"""

import base64
from urllib.parse import parse_qs, urlparse

from selectolax.parser import HTMLParser

from .common import ParseError, settle, text

NAME = "bing"
HOME = "https://www.bing.com/"
URL = "https://www.bing.com/search"


def unwrap(href):
    """some bing links are /ck/a?...&u=a1<base64url of the real url>."""
    p = urlparse(href)
    if p.netloc.endswith("bing.com") and p.path.startswith("/ck/"):
        u = parse_qs(p.query).get("u", [""])[0]
        if u.startswith("a1"):
            raw = u[2:]
            try:
                return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8", "replace")
            except ValueError:
                return href
    return href


def parse(html):
    tree = HTMLParser(html)
    if tree.css_first("li.b_no") or tree.css_first("ol#b_results li.b_no"):
        return []
    hits = []
    for li in tree.css("li.b_algo"):
        a = li.css_first("h2 a")
        if a is None:
            continue
        snippet = li.css_first("div.b_caption p") or li.css_first("p")
        hits.append({
            "title": text(a),
            "url": unwrap(a.attributes.get("href") or ""),
            "snippet": text(snippet),
        })
    # a real "no results" page has li.b_no and returned above, so a full page
    # without hits is markup drift, with or without its ol#b_results
    if not hits and len(html) > 20000:
        raise ParseError("no li.b_algo on a full page")
    return hits


async def search(http, query, lang, region, n):
    cc = region.upper()
    params = {"q": query, "setlang": lang, "count": n, "first": 1, "FORM": "QBLH"}
    if cc:
        # without a country, bing picks the market from the language alone
        params.update(cc=cc, mkt=f"{lang}-{cc}")
    resp = await http.get(NAME, URL, lang=lang, referer=HOME, params=params, check=False)
    return settle(resp, parse, query)[:n]
