"""habr, the russian-language developer community, through the search api its
own site uses (no login). /ru/articles/{id}/ redirects to the canonical page
of news and company posts too."""

from .common import api_json, plain

NAME = "habr"
HOME = "https://habr.com/"


async def search(http, query, lang, region, n):
    data = await api_json(http, NAME, "https://habr.com/kek/v2/articles/", query, lang="ru",
                          params={"query": query, "order": "relevance", "fl": "ru", "hl": "ru", "page": 1})
    refs = data.get("publicationRefs") or {}
    hits = []
    for pid in (data.get("publicationIds") or [])[:n]:
        ref = refs.get(pid) or {}
        stats = ref.get("statistics") or {}
        facts = [plain((ref.get("leadData") or {}).get("textHtml"))[:200], f"score {stats.get('score', 0)}",
                 f"{stats.get('commentsCount', 0)} comments", (ref.get("timePublished") or "")[:10]]
        hits.append({"title": plain(ref.get("titleHtml")), "url": f"https://habr.com/ru/articles/{pid}/",
                     "snippet": " · ".join(f for f in facts if f)})
    return hits
