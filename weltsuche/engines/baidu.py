"""baidu. the first request of a fresh session opens the homepage, the way a
person does, so the search carries a BAIDUID cookie."""

from selectolax.parser import HTMLParser

from .common import ParseError, settle, text

NAME = "baidu"
HOME = "https://www.baidu.com/"
URL = "https://www.baidu.com/s"

# baidu's class names carry a build hash (content-right_8Zs40), so match on the
# stable prefix. the last entries are the older markup, still seen on some
# result templates.
_SNIPPET = (
    "[class*='content-right']", "[class*='summary-text']", "[class*='abstract']",
    "div.c-abstract", "[class*='c-span-last']", "[class*='content_']",
)


def parse(html):
    tree = HTMLParser(html)
    if "很抱歉，没有找到" in html or "没有找到相关结果" in html:
        return []
    hits = []
    for node in tree.css("div.result, div.result-op"):
        if "c-container" not in (node.attributes.get("class") or ""):
            continue
        a = node.css_first("h3 a")
        if a is None:
            continue
        # `mu` is the real destination; the href is a baidu redirect. baidu's
        # own aggregate cards (精选笔记 and friends) carry the literal string
        # "null" there, and their href is a baidu-internal page: not a hit.
        mu = node.attributes.get("mu") or ""
        url = mu if mu.startswith("http") else (a.attributes.get("href") or "")
        if not url.startswith("http") or mu == "null":
            continue
        snippet = None
        for sel in _SNIPPET:
            snippet = node.css_first(sel)
            if snippet is not None and text(snippet):
                break
        if snippet is None or not text(snippet):
            # no dedicated abstract element: take the container text minus the title
            body = text(node)
            snippet_text = body.replace(text(a), "", 1).strip()
        else:
            snippet_text = text(snippet)
        hits.append({
            "title": text(a),
            "url": url,
            "snippet": snippet_text,
        })
    if not hits and len(html) > 20000:
        raise ParseError("no div.result.c-container on a full page")
    return hits


async def search(http, query, lang, region, n):
    if not await http.has_cookie(NAME, "BAIDUID"):
        await http.get(NAME, HOME, lang="zh", check=False)
    params = {"wd": query, "ie": "utf-8", "rn": n, "tn": "baidu"}
    resp = await http.get(NAME, URL, lang="zh", referer=HOME, params=params, check=False)
    return settle(resp, parse, query)[:n]
