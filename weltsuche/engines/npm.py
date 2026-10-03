"""npm packages (javascript, typescript, node) through the registry's own
search api (no login)."""

from .common import api_json

NAME = "npm"
HOME = "https://www.npmjs.com/"


async def search(http, query, lang, region, n):
    data = await api_json(http, NAME, "https://registry.npmjs.org/-/v1/search", query, params={"text": query, "size": n})
    hits = []
    for found in data.get("objects") or []:
        package = found.get("package") or {}
        weekly = (found.get("downloads") or {}).get("weekly")
        facts = [package.get("description") or "", f"v{package['version']}" if package.get("version") else "",
                 f"{weekly:,} downloads a week" if isinstance(weekly, int) else "", (package.get("date") or "")[:10]]
        hits.append({"title": package.get("name") or "",
                     "url": (package.get("links") or {}).get("npm") or f"{HOME}package/{package.get('name')}",
                     "snippet": " · ".join(f for f in facts if f)})
    return hits
