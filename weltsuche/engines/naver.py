"""naver, korea's own search engine, through its web results page.

naver's class names are generated per build, so the parser reads the
click-tracking attribute `data-heatmap-target` instead, which names what an
anchor is (".link" on the web tab, ".nblg" on the blog tab), and groups a
result's anchors by their url: the site's breadcrumb (it has a "›"), the
title, then the snippet."""

from selectolax.parser import HTMLParser

from .common import ParseError, settle, text

NAME = "naver"
HOME = "https://www.naver.com/"
URL = "https://search.naver.com/search.naver"

# "opens in a new window", a screen-reader suffix on every result anchor
_NEW_WINDOW = "새 창 열림"
# "there are no search results"
_NO_RESULTS = "검색결과가 없습니다"


def parse(html, target=".link"):
    groups = {}
    for a in HTMLParser(html).css(f'a[data-heatmap-target="{target}"]'):
        href = a.attributes.get("href") or ""
        if not href.startswith("http"):
            continue
        words = text(a).replace(_NEW_WINDOW, "").strip()
        parts = groups.setdefault(href, [])
        if words and "›" not in words:
            parts.append(words)
    hits = [{"title": parts[0], "url": href, "snippet": parts[1] if len(parts) > 1 else ""}
            for href, parts in groups.items() if parts]
    if not hits:
        if _NO_RESULTS in html:
            return []
        if len(html) > 20000:
            raise ParseError(f'no a[data-heatmap-target="{target}"] on a full page')
    return hits


async def search(http, query, lang, region, n):
    resp = await http.get(NAME, URL, lang="ko", referer=HOME, params={"where": "web", "query": query}, check=False)
    return settle(resp, parse, query)[:n]
