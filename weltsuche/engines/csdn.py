"""csdn, china's largest developer blog site, through the search api its own
site uses (no login). titles come with <em> highlights and urls with
tracking parameters; both are removed."""

from urllib.parse import urlsplit

from .common import api_json, plain

NAME = "csdn"
HOME = "https://www.csdn.net/"


async def search(http, query, lang, region, n):
    data = await api_json(http, NAME, "https://so.csdn.net/api/v3/search", query, lang="zh",
                          params={"q": query, "t": "blog", "p": 1})
    hits = []
    for item in (data.get("result_vos") or [])[:n]:
        parts = urlsplit(item.get("url") or "")
        facts = [plain(item.get("description"))[:200], f"{item.get('view', 0)} views", item.get("nickname") or ""]
        hits.append({"title": plain(item.get("title")), "url": f"{parts.scheme}://{parts.netloc}{parts.path}",
                     "snippet": " · ".join(f for f in facts if f)})
    return hits
