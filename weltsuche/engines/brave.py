"""brave search, own index. country and safesearch travel as cookies."""

from selectolax.parser import HTMLParser

from .common import ParseError, settle, text

NAME = "brave"
HOME = "https://search.brave.com/"
URL = "https://search.brave.com/search"


def parse(html):
    tree = HTMLParser(html)
    hits = []
    for node in tree.css("div.snippet"):
        # the ai answer box and news/video blocks are also div.snippet; only
        # data-type="web" is an organic hit
        if node.attributes.get("data-type") != "web":
            continue
        a = node.css_first("a[href^='http']")
        if a is None:
            continue
        title = node.css_first(".title") or a
        desc = (node.css_first(".generic-snippet .content") or node.css_first(".snippet-description")
                or node.css_first(".snippet-content") or node.css_first(".description"))
        hits.append({
            "title": text(title),
            "url": a.attributes.get("href") or "",
            "snippet": text(desc),
        })
    if not hits:
        # brave's script bundle carries these phrases on every page, so only
        # the page's visible text counts (found in review 2026-09-29)
        tree.strip_tags(["script", "style", "noscript", "template"], recursive=True)
        low = text(tree.body).lower()
        if "no results found" in low or "not many great matches" in low:
            return []
        if len(html) > 20000:
            raise ParseError("no div.snippet on a full page")
    return hits


async def search(http, query, lang, region, n):
    # "all" is brave's own setting for results from every country
    cookies = {"country": region or "all", "safesearch": "moderate", "useLocation": "0"}
    resp = await http.get(NAME, URL, lang=lang, referer=HOME, params={"q": query, "source": "web"}, cookies=cookies, check=False)
    return settle(resp, parse, query)[:n]
