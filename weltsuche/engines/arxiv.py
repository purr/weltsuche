"""arxiv, the preprint server for physics, mathematics, computer science
and related fields, through its export api (atom xml, no login). arxiv asks
for one request every three seconds; ENGINE_GAP_S keeps more than that.

plain words must all appear (`all:a AND all:b`); a query in arxiv's own
syntax (ti:, au:, abs:, cat:, AND, OR) is passed as it is."""

import re
from xml.etree import ElementTree

from .. import net
from .common import ParseError, QueryError

NAME = "arxiv"
HOME = "https://arxiv.org/"
API = "https://export.arxiv.org/api/query"

_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
_OWN_SYNTAX = re.compile(r"\b(?:ti|au|abs|co|jr|cat|rn|id|all):|\b(?:AND|OR|ANDNOT)\b")
_TERM = re.compile(r'"[^"]+"|\S+')


def search_query(query):
    if _OWN_SYNTAX.search(query):
        return query
    return " AND ".join(f"all:{term}" for term in _TERM.findall(query))


def parse(xml):
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as e:
        raise ParseError(f"not an atom feed: {e}") from e
    hits = []
    for entry in root.findall("a:entry", _NS):
        url = entry.findtext("a:id", "", _NS).strip()
        summary = " ".join(entry.findtext("a:summary", "", _NS).split())
        if "/api/errors" in url:
            # arxiv answers a malformed query with one entry that explains it
            raise QueryError(f"arxiv: {summary}")
        authors = [a.findtext("a:name", "", _NS) for a in entry.findall("a:author", _NS)]
        category = entry.find("arxiv:primary_category", _NS)
        facts = [summary[:200], entry.findtext("a:published", "", _NS)[:10],
                 ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else ""),
                 category.get("term", "") if category is not None else ""]
        hits.append({"title": " ".join(entry.findtext("a:title", "", _NS).split()),
                     "url": url.replace("http://", "https://", 1), "snippet": " · ".join(f for f in facts if f)})
    return hits


async def search(http, query, lang, region, n):
    resp = await http.get(NAME, API, lang="en", document=False, check=False,
                          params={"search_query": search_query(query), "max_results": n, "sortBy": "relevance"})
    net.classify(resp, query, markers=False)
    return parse(resp.text)
