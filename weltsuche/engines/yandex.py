"""yandex. captchas automated clients readily; when it does, the breaker parks
it for an hour and the other engines carry the query."""

from urllib.parse import parse_qs, urlsplit

from selectolax.parser import HTMLParser

from .common import ParseError, settle, text

NAME = "yandex"
HOME = "https://yandex.com/"

# yandex's own widgets (the alice ai answer, its services) share the organic
# markup; they are not web results
_OWN = ("yandex.ru/alice", "yandex.com/alice", "ya.ru/alice")


def unwrap(url):
    """yandex lists some foreign pages through its own translator
    (tr-page.yandex.ru/translate?lang=en-ru&url=<page>): the source is the
    page itself, an english techradar review in the case that was seen."""
    parts = urlsplit(url)
    if parts.netloc.startswith("tr-page.yandex.") and parts.path.startswith("/translate"):
        return parse_qs(parts.query).get("url", [url])[0]
    return url


def parse(html):
    tree = HTMLParser(html)
    hits = []
    for node in tree.css("li.serp-item"):
        a = node.css_first("a.OrganicTitle-Link") or node.css_first("h2 a") or node.css_first("a[href^='http']")
        if a is None:
            continue
        url = unwrap(a.attributes.get("href") or "")
        if any(own in url for own in _OWN):
            continue
        title = node.css_first("h2") or a
        desc = (node.css_first(".OrganicTextContentSpan") or node.css_first(".Organic-ContentWrapper")
                or node.css_first(".TextContainer") or node.css_first(".text-container"))
        hits.append({
            "title": text(title),
            "url": url,
            "snippet": text(desc),
        })
    if not hits:
        low = html.lower()
        if "ничего не нашлось" in low or "nothing found" in low:
            return []
        if len(html) > 20000:
            raise ParseError("no li.serp-item on a full page")
    return hits


async def search(http, query, lang, region, n):
    host = "https://yandex.ru" if lang in ("ru", "uk", "be", "kk") else "https://yandex.com"
    resp = await http.get(NAME, f"{host}/search/", lang=lang, referer=host + "/", params={"text": query, "lang": lang}, check=False)
    return settle(resp, parse, query)[:n]
