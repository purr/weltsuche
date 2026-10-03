"""qiita, japan's developer articles, through its own search page, which
carries the results as json for its react component, best match first. the
public api (/api/v2/items) sorts by date: for "Python 非同期" it answered
with this week's articles that merely mention both words (2026-09-29)."""

import json

from selectolax.parser import HTMLParser

from .common import ParseError, plain, settle

NAME = "qiita"
HOME = "https://qiita.com/"
URL = "https://qiita.com/search"


def parse(html):
    node = HTMLParser(html).css_first('script[data-component-name="ArticleSearchSearchPage"]')
    if node is None:
        if len(html) > 20000:
            raise ParseError("no ArticleSearchSearchPage payload on a full page")
        return []
    try:
        items = (json.loads(node.text()).get("searchResult") or {}).get("items") or []
    except ValueError as e:
        raise ParseError(f"the search payload is not json: {e}") from e
    hits = []
    for item in items:
        user = (item.get("author") or {}).get("urlName", "")
        tags = ", ".join(t.get("name", "") for t in item.get("tags") or [])
        facts = [plain(item.get("snippet"))[:200], f"{item.get('likesCount', 0)} likes",
                 (item.get("createdAt") or "")[:10], f"tags: {tags}" if tags else ""]
        hits.append({"title": plain(item.get("title")), "url": f"https://qiita.com/{user}/items/{item.get('uuid')}",
                     "snippet": " · ".join(f for f in facts if f)})
    return hits


async def search(http, query, lang, region, n):
    resp = await http.get(NAME, URL, lang="ja", referer=HOME, params={"q": query, "sort": "rel"}, check=False)
    return settle(resp, parse, query)[:n]
