"""grep.app: exact code search across a million public github repositories,
no login. best for literal strings (a function name, an error message), not
for natural-language questions."""

from selectolax.parser import HTMLParser

from .common import api_json

NAME = "grepapp"
HOME = "https://grep.app/"


def code_lines(snippet):
    """the matched lines of a snippet table as "L88 code; L89 code". the code
    is syntax-highlighted token by token, so it is read without separators."""
    lines = []
    for row in HTMLParser(snippet or "").css("tr"):
        pre = row.css_first("pre")
        if pre is not None:
            lines.append(f"L{row.attributes.get('data-line', '?')} " + " ".join(pre.text(separator="").split()))
    return "; ".join(lines)


async def search(http, query, lang, region, n):
    data = await api_json(http, NAME, "https://grep.app/api/search", query, params={"q": query, "page": 1})
    hits = []
    for hit in ((data.get("hits") or {}).get("hits") or [])[:n]:
        repo, branch, path = hit.get("repo", ""), hit.get("branch", ""), hit.get("path", "")
        hits.append({"title": f"{repo}: {path}", "url": f"https://github.com/{repo}/blob/{branch}/{path}",
                     "snippet": code_lines((hit.get("content") or {}).get("snippet"))})
    return hits
