"""duckduckgo, html endpoint. bing index. honours a region code (kl)."""

from urllib.parse import parse_qs, unquote, urlparse

from selectolax.parser import HTMLParser

from .common import ParseError, settle, text

NAME = "duckduckgo"
HOME = "https://duckduckgo.com/"
URL = "https://html.duckduckgo.com/html/"

# ddg region codes are country-language but with a few of their own spellings
_SPECIAL = {
    ("en", "us"): "us-en", ("en", "gb"): "uk-en", ("en", "au"): "au-en",
    ("ja", "jp"): "jp-jp", ("ko", "kr"): "kr-kr", ("pt", "br"): "br-pt",
    ("zh", "cn"): "cn-zh", ("zh", "tw"): "tw-tzh", ("zh", "hk"): "hk-tzh",
    ("ar", "sa"): "xa-ar", ("es", "us"): "us-es",
}


def region_code(lang, region):
    """ddg's kl for a language and country; "wt-wt" is its "no region"."""
    if not region:
        return "wt-wt"
    return _SPECIAL.get((lang, region), f"{region}-{lang}")


def unwrap(href):
    """ddg wraps every result in //duckduckgo.com/l/?uddg=<url>."""
    if href.startswith("//"):
        href = "https:" + href
    p = urlparse(href)
    if p.netloc.endswith("duckduckgo.com") and p.path.startswith("/l/"):
        return unquote(parse_qs(p.query).get("uddg", [""])[0])
    return href


def parse(html):
    tree = HTMLParser(html)
    if tree.css_first("div.no-results"):
        return []
    hits = []
    for node in tree.css("div.result"):
        if "result--ad" in (node.attributes.get("class") or ""):
            continue
        a = node.css_first("a.result__a")
        if a is None:
            continue
        snippet = node.css_first("a.result__snippet") or node.css_first("div.result__snippet")
        hits.append({
            "title": text(a),
            "url": unwrap(a.attributes.get("href") or ""),
            "snippet": text(snippet),
        })
    if not hits and len(html) > 5000:
        raise ParseError("no div.result on a full page")
    return hits


async def search(http, query, lang, region, n):
    resp = await http.get(NAME, URL, lang=lang, referer=HOME, params={"q": query, "kl": region_code(lang, region)}, check=False)
    return settle(resp, parse, query)[:n]
