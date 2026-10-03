"""gitlab.com projects through its api (no login). most starred matches first."""

from .common import api_json

NAME = "gitlab"
HOME = "https://gitlab.com/"


async def search(http, query, lang, region, n):
    projects = await api_json(http, NAME, "https://gitlab.com/api/v4/projects", query,
                              params={"search": query, "order_by": "star_count", "sort": "desc", "per_page": n})
    hits = []
    for p in projects if isinstance(projects, list) else []:
        facts = [p.get("description") or "", f"★{p.get('star_count', 0)}"]
        if p.get("topics"):
            facts.append("topics: " + ", ".join(p["topics"][:8]))
        if p.get("last_activity_at"):
            facts.append("active " + p["last_activity_at"][:10])
        hits.append({"title": p.get("path_with_namespace", ""), "url": p.get("web_url", ""),
                     "snippet": " · ".join(f for f in facts if f)})
    return hits
