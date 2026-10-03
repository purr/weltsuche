"""code on github through github's code search api: exact names, calls,
error messages (`FastMCP language:python`, `repo:owner/name hasCacheBits`).
needs a github login, borrowed from the github cli (see github.py); 10
requests a minute."""

from . import github

NAME = "github_code"
HOME = "https://github.com/"


async def search(http, query, lang, region, n):
    data = await github.api(http, NAME, "/search/code", {"q": query, "per_page": n},
                            accept="application/vnd.github.text-match+json", require_login=True)
    hits = []
    for item in data.get("items") or []:
        repo = (item.get("repository") or {}).get("full_name", "")
        fragments = [m.get("fragment", "") for m in item.get("text_matches") or []]
        hits.append({"title": f"{repo}: {item.get('path', '')}", "url": item.get("html_url", ""),
                     "snippet": " … ".join(" ".join(f.split()) for f in fragments[:2])})
    return hits
