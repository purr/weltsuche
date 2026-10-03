"""zenn, japan's developer articles and books, through the search api its own
site uses (no login)."""

from .common import api_json

NAME = "zenn"
HOME = "https://zenn.dev/"


async def search(http, query, lang, region, n):
    data = await api_json(http, NAME, "https://zenn.dev/api/search", query, lang="ja",
                          params={"q": query, "source": "articles"})
    hits = []
    for article in (data.get("articles") or [])[:n]:
        user = (article.get("user") or {}).get("username", "")
        facts = [f"{article.get('liked_count', 0)} likes", (article.get("published_at") or "")[:10], f"by {user}" if user else ""]
        hits.append({"title": article.get("title") or "", "url": "https://zenn.dev" + (article.get("path") or ""),
                     "snippet": " · ".join(f for f in facts if f)})
    return hits
