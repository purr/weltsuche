"""codeberg, the forgejo forge of the free-software community, through its
repository search api (no login). most starred matches first."""

from .common import api_json

NAME = "codeberg"
HOME = "https://codeberg.org/"


async def search(http, query, lang, region, n):
    data = await api_json(http, NAME, "https://codeberg.org/api/v1/repos/search", query,
                          params={"q": query, "limit": n, "includeDesc": "true", "sort": "stars", "order": "desc"})
    hits = []
    for repo in data.get("data") or []:
        facts = [repo.get("description") or "", f"★{repo.get('stars_count', 0)}", repo.get("language") or ""]
        if repo.get("updated_at"):
            facts.append("updated " + repo["updated_at"][:10])
        hits.append({"title": repo.get("full_name", ""), "url": repo.get("html_url", ""),
                     "snippet": " · ".join(f for f in facts if f)})
    return hits
