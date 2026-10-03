"""pubmed, the us national library of medicine's index of biomedical and
life-science papers, through ncbi's e-utilities (no key: 3 requests a
second). the ids of the best matches first, then their summaries."""

from .common import QueryError, api_json

NAME = "pubmed"
HOME = "https://pubmed.ncbi.nlm.nih.gov/"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"


async def search(http, query, lang, region, n):
    found = await api_json(http, NAME, EUTILS + "esearch.fcgi", query,
                           params={"db": "pubmed", "term": query, "retmode": "json", "retmax": n, "sort": "relevance"})
    result = found.get("esearchresult") or {}
    if result.get("ERROR"):
        # e-utilities reports a malformed query at http 200, in ERROR
        raise QueryError(f"pubmed: {result['ERROR']}")
    ids = result.get("idlist") or []
    if not ids:
        return []
    summaries = (await api_json(http, NAME, EUTILS + "esummary.fcgi", query, spaced=False,
                                params={"db": "pubmed", "id": ",".join(ids), "retmode": "json"})).get("result") or {}
    hits = []
    for uid in ids:
        doc = summaries.get(uid) or {}
        authors = [a.get("name", "") for a in doc.get("authors") or []]
        facts = [doc.get("source") or "", doc.get("pubdate") or "",
                 ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else ""),
                 ", ".join(doc.get("pubtype") or []), doc.get("elocationid") or ""]
        hits.append({"title": doc.get("title") or "", "url": f"{HOME}{uid}/",
                     "snippet": " · ".join(f for f in facts if f)})
    return hits
