"""rust crates through crates.io's api (no login). crates.io asks api
clients to name themselves in the user-agent instead of passing as a
browser (crates.io/data-access), so this engine does."""

from .common import api_json

NAME = "crates"
HOME = "https://crates.io/"
USER_AGENT = "weltsuche (multilingual search for claude code)"


async def search(http, query, lang, region, n):
    data = await api_json(http, NAME, "https://crates.io/api/v1/crates", query, params={"q": query, "per_page": n},
                          headers={"User-Agent": USER_AGENT})
    hits = []
    for crate in data.get("crates") or []:
        version = crate.get("default_version") or crate.get("max_version") or ""
        facts = [crate.get("description") or "", f"v{version}" if version else "",
                 f"{crate.get('recent_downloads') or 0:,} downloads in 90 days", (crate.get("updated_at") or "")[:10]]
        hits.append({"title": crate.get("name") or "", "url": f"{HOME}crates/{crate.get('name')}",
                     "snippet": " · ".join(" ".join(f.split()) for f in facts if f)})
    return hits
