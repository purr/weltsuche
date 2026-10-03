"""stack overflow through the stack exchange api's excerpt search (no
login; 300 requests a day per address, shared with fetch's stack exchange
route). the language picks the edition: ru, ja, pt and es have their own
stack overflow. every hit links to its question, which fetch reads with
its answers."""

import html
import re

from .. import net
from .common import decode_json, plain

NAME = "stackexchange"
HOME = "https://stackoverflow.com/"
API = "https://api.stackexchange.com/2.3/search/excerpts"

EDITIONS = {"ru": "ru.stackoverflow", "ja": "ja.stackoverflow", "pt": "pt.stackoverflow", "es": "es.stackoverflow"}

# the excerpt marks matches with <span class="highlight">; removed before
# the text is taken, so a match inside a japanese word adds no space
_HIGHLIGHT = re.compile(r"</?span\b[^>]*>")


# the api's throttle answer (http 400, error_name throttle_violation) says
# "... more requests available in 1234 seconds"
_AVAILABLE_IN = re.compile(r"available in (\d+) seconds")


async def search(http, query, lang, region, n):
    site = EDITIONS.get(lang, "stackoverflow")
    params = {"q": query, "site": site, "order": "desc", "sort": "relevance", "pagesize": n}
    resp = await http.request(NAME, "GET", API, lang=lang, document=False, check=False, params=params)
    if resp.status_code == 400:
        try:
            said = resp.json()
        except ValueError:
            said = {}  # no json: classify judges the status
        if said.get("error_name") == "throttle_violation":
            wait = _AVAILABLE_IN.search(said.get("error_message") or "")
            raise net.RateLimited(f"stack exchange quota: {said.get('error_message')}", int(wait.group(1)) if wait else None)
    net.classify(resp, query, markers=False)
    data = decode_json(resp, query)
    if data.get("backoff"):
        # the api asks for a pause before the next request; the breaker keeps
        # it, up to the longest rest weltsuche takes
        await http.breakers.open(NAME, min(int(data["backoff"]), http.config.BREAKER_BLOCK_S),
                           "the stack exchange api asked for a pause")
    hits = []
    for item in data.get("items") or []:
        answer = item.get("item_type") == "answer"
        facts = [plain(_HIGHLIGHT.sub("", item.get("excerpt") or ""))[:200],
                 f"{'an answer' if answer else 'the question'} matched, score {item.get('score', 0)}",
                 "accepted" if item.get("is_accepted") else "",
                 f"{item.get('answer_count', 0)} answers" if not answer else "",
                 "tags: " + ", ".join(item["tags"]) if item.get("tags") else ""]
        hits.append({"title": html.unescape(item.get("title") or ""),
                     "url": f"https://{site}.com/questions/{item.get('question_id')}",
                     "snippet": " · ".join(f for f in facts if f)})
    return hits
