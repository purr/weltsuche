"""v2ex, the chinese forum where developers show what they built, through
sov2ex, the public search engine the community runs for it (no login)."""

from .common import api_json

NAME = "v2ex"
HOME = "https://www.v2ex.com/"


async def search(http, query, lang, region, n):
    data = await api_json(http, NAME, "https://www.sov2ex.com/api/search", query, lang="zh",
                          params={"q": query, "size": n})
    hits = []
    for hit in data.get("hits") or []:
        topic = hit.get("_source") or {}
        facts = [" ".join((topic.get("content") or "").split())[:200], f"{topic.get('replies', 0)} replies",
                 (topic.get("created") or "")[:10]]
        hits.append({"title": topic.get("title") or "", "url": f"https://www.v2ex.com/t/{topic.get('id')}",
                     "snippet": " · ".join(f for f in facts if f)})
    return hits
