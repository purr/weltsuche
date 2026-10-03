"""what the engine modules share: their errors, the json request of an api
engine, the judging of a results page, and turning html snippets and raw hits
into text and models.Hit.

a web engine requests with check=False and hands the response to `settle`,
which judges it by status first, parses it, and looks for refusal markers
only when the page yielded no hits: a results page whose snippets quote
"captcha" is still a results page. parsing returns an empty list when the
page has a "no results" marker, and raises `ParseError` when the page is big
but nothing matched, because that means the markup changed and the parser
needs a fix, not that the web is empty. an api engine (github, habr, ...)
reads json through `api_json`.
"""

import math
import re
import unicodedata
from datetime import date

from selectolax.parser import HTMLParser

from .. import net
from ..models import Hit


class ParseError(Exception):
    """the page came back but the selectors found nothing. markup drift."""


class QueryError(Exception):
    """the engine rejected the query itself (github: http 422 for a
    malformed qualifier). no breaker: the next query may be fine."""


_YES = ("1", "true", "yes")
_NO = ("0", "false", "no")


def describe_params(allowed):
    """an engine's PARAMS as one line: name (choices or kind)."""
    def kind(k):
        return "|".join(k) if isinstance(k, tuple) else "yyyy-mm-dd" if k is date else k.__name__
    return ", ".join(f"{name} ({kind(k)})" for name, k in allowed.items())


def check_params(engine, allowed, params):
    """(the params `engine` takes, converted; the names it does not take).
    `allowed` is the engine's PARAMS: name -> a tuple of choices, or a kind
    (float, int, bool, str, date). a value that does not fit raises
    QueryError naming what would."""
    used, unused = {}, []
    for name, value in (params or {}).items():
        k = allowed.get(name)
        if k is None:
            unused.append(name)
            continue
        try:
            if isinstance(k, tuple):
                if str(value) not in k:
                    raise ValueError
                used[name] = str(value)
            elif k is bool:
                # a word that is neither yes nor no is refused: read as no,
                # `sold="ja"` turned the filter off without a word
                word = str(value).strip().lower()
                if not isinstance(value, bool) and word not in _YES + _NO:
                    raise ValueError
                used[name] = value if isinstance(value, bool) else word in _YES
            elif k is date:
                used[name] = date.fromisoformat(str(value)).isoformat()
            elif k in (int, float):
                # prices, counts and distances: nan, inf and negatives would
                # go out as `low-price=nan`
                used[name] = k(value)
                if not math.isfinite(used[name]) or used[name] < 0:
                    raise ValueError
            else:
                used[name] = k(value)
        except (TypeError, ValueError):
            raise QueryError(f"{engine}: {name}={value!r} does not fit; it takes {describe_params(allowed)}") from None
    return used, unused


def amount(value):
    """a number as a url parameter: 300.0 as "300", 299.5 as "299.5", and
    1500000.0 as "1500000", where format(1500000.0, "g") gives "1.5e+06"."""
    return f"{value:f}".rstrip("0").rstrip(".")


class NeedsLogin(Exception):
    """the engine works only signed in (github code search). no breaker: it
    answers once the login exists."""


async def api_json(http, name, url, query, *, lang="en", params=None, json_body=None, method="GET", headers=None,
                   spaced=True):
    """one json api request through the engine's session: refusals by status
    (429, 403, 5xx) raise as for a results page; an answer that is not json
    is drift. `spaced=False` for the second request of one search (the
    details after the ids), which a person's browser sends without a pause."""
    resp = await http.request(name, method, url, lang=lang, document=False, check=False, params=params,
                              json_body=json_body, extra_headers=headers, spaced=spaced)
    net.classify(resp, query, markers=False)
    return decode_json(resp, query)


def decode_json(resp, query=""):
    """the json of an api answer. an answer that is not json is a challenge
    page when it reads like one (grep.app answers clients it distrusts that
    way), else drift."""
    try:
        return resp.json()
    except ValueError as e:
        net.classify(resp, query)
        raise ParseError(f"answered http {resp.status_code} without json ({resp.headers.get('content-type', 'no content type')})") from e


_HIGHLIGHT = re.compile(r"</?(?:em|mark|b|strong)\b[^>]*>", re.I)


def plain(html):
    """visible text of an html snippet, with a space at every tag boundary so
    words in neighbouring elements do not glue together ("Python'sasyncio").
    search highlights (<em>, <b>, <mark>, <strong>) are removed first: they
    sit inside a word as often as around one, and a space there split
    chinese words and russian compounds ("爬虫 的进阶", "Python -приложение",
    csdn, habr, startpage and baidu, 2026-09-29)."""
    body = HTMLParser(_HIGHLIGHT.sub("", html or "")).body
    return " ".join(body.text(separator=" ").split()) if body is not None else ""


def text(node):
    """`plain` for a parsed node."""
    return plain(node.html) if node is not None else ""


def settle(resp, parse, query):
    """hits from a results page: status and url checked first, then the
    parser; refusal markers only when the page yielded nothing."""
    net.classify(resp, query, markers=False)
    try:
        hits = parse(resp.text)
    except ParseError:
        net.classify(resp, query)
        raise
    if not hits:
        net.classify(resp, query)
    return hits


def clean_hits(raw, n):
    """raw hits to models.Hit: absolute http urls only, strings trimmed,
    capped at n. a parser that found hits of which none survive has drifted
    (relative links, say), which must not read as "no results"."""
    out = []
    for h in raw:
        url = (h.get("url") or "").strip()
        if not url.startswith("http"):
            continue
        out.append(Hit(
            title=" ".join((h.get("title") or "").split())[:300],
            url=url,
            snippet=" ".join((h.get("snippet") or "").split())[:600],
        ))
        if len(out) >= n:
            break
    if raw and not out:
        raise ParseError(f"{len(raw)} hits parsed, none with an absolute http url (first: {raw[0].get('url')!r})")
    return out


# scripts written without spaces between words (chinese, japanese, thai,
# lao, burmese, khmer) and hangul: a query's words cannot be told apart, so
# every two-character window of a run counts too
_UNSPACED = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯\u0e00-\u0eff\u1000-\u109f\u1780-\u17ff]+")
_NUMBER = re.compile(r"\d{3,}")


def _words(text):
    """runs of letters and the marks that belong to them. a hindi or bengali
    vowel sign is a combining mark, which ends a word for the regex \\w:
    "समीक्षा" fell apart into pieces under three letters, and focus and the
    decoy check saw no word at all (found in review 2026-10-03)."""
    run = []
    for ch in text + " ":
        if unicodedata.category(ch)[0] in "LM":
            run.append(ch)
        elif run:
            yield "".join(run)
            run = []


def query_tokens(query):
    """the parts of a query a real result would have to mention: words of
    three letters or more, numbers of three digits or more, and every
    two-character window of a run in a script without spaces."""
    q = query.lower()
    tokens = {w for w in _words(q) if sum(unicodedata.category(c)[0] == "L" for c in w) >= 3}
    tokens.update(_NUMBER.findall(q))
    for run in _UNSPACED.findall(q):
        if len(run) > 1:
            tokens.update(run[i:i + 2] for i in range(len(run) - 1))
        else:
            tokens.add(run)
    return tokens


def looks_relevant(query, hits):
    """false when no hit mentions the query at all.

    bing in particular answers a client it distrusts with a perfectly formed
    results page for some other query. a page where none of the hits contain
    a single query token is a decoy, not an answer, and must not be cached.
    """
    tokens = query_tokens(query)
    if not tokens or not hits:
        return True
    return any(any(t in f"{h.title} {h.snippet} {h.url}".lower() for t in tokens) for h in hits)
