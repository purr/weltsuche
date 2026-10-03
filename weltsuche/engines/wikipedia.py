"""wikipedia's search api for the language edition asked for. structured json,
no parsing risk, and a good first stop when learning a topic."""

import html
import re
from urllib.parse import quote

from curl_cffi.requests.exceptions import DNSError

from .. import net
from .common import QueryError

NAME = "wikipedia"
HOME = None

# how much a hit counts when results from several engines are merged (1.0 is
# a web engine). wikipedia's full-text search lists any article that mentions
# a word of the query, so for a question-shaped query its hits are weak
# evidence: generic articles took ranks 2, 4 and 6 of 6 for a hebrew tariff
# query (measured 2026-09-29).
WEIGHT = 0.5

_TAGS = re.compile(r"<[^>]+>")


async def search(http, query, lang, region, n):
    url = f"https://{lang}.wikipedia.org/w/api.php"
    params = {
        "action": "query", "list": "search", "srsearch": query, "format": "json",
        "utf8": 1, "srlimit": n, "srprop": "snippet",
    }
    try:
        resp = await http.get(NAME, url, lang=lang, document=False, params=params, check=False)
    except DNSError as e:
        # no such language edition ("fil" is tl.wikipedia.org): a fault of
        # this query's language, not a reason to rest wikipedia for all
        raise QueryError(f"there is no {lang}.wikipedia.org") from e
    # a json answer has no challenge phrases to look for; a snippet mentioning
    # captcha must not park wikipedia (found in review 2026-09-29)
    net.classify(resp, query, markers=False)
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"wikipedia api error: {data['error'].get('info', data['error'])}")
    hits = []
    for item in data.get("query", {}).get("search", []):
        title = item.get("title", "")
        hits.append({
            "title": title,
            "url": f"https://{lang}.wikipedia.org/wiki/" + quote(title.replace(" ", "_")),
            "snippet": html.unescape(_TAGS.sub("", item.get("snippet", ""))),
        })
    return hits
