"""read a page the way a person would: load it with a browser fingerprint, keep
the readable text, page through it in chunks.

what goes wrong when reading arbitrary pages, and what this does about it
(each case measured 2026-09-29):
- a url off the public web (file://, localhost, 192.168.x.x): refused, see
  net.vet_url. a fetched page can ask the model to fetch something.
- an anubis proof-of-work gate (bugs.winehq.org): paid, as a browser pays it.
- any other bot wall served with http 200 (reddit's "prove your humanity"):
  reported as blocked and not cached, instead of returned as the article.
- a charset declared only in <meta> (kakaku.com, shift_jis): decoded by that
  declaration, as a browser does, instead of as utf-8 mojibake.
- an extractor that keeps a fragment of a long document (rfc 9110: 5,220 of
  about 450,000 characters): the full visible text, with a note saying so.
- a page built by javascript (smzdm): its meta description and a note.
- a site that walls off plain clients (reddit, x, youtube, instagram, tiktok,
  threads, bluesky, stack exchange): its routes in sites.py, raced here.

every answer is a models.Page. a page that could not be read carries
`error`, a degraded one says so in `notes`, and routes that failed before
the answer are listed in `tried`. nothing that failed is cached.
"""

import asyncio
import io
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urlsplit

import trafilatura
from curl_cffi.requests.exceptions import RequestException
from pydantic import ValidationError
from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError

from . import anubis, frontends, net, sites
from .engines.common import query_tokens
from .models import FetchReply, Page
from .text import decode_html, head_meta, is_html, is_text, tidy, visible_text

log = logging.getLogger("weltsuche.fetch")

_TLD_LANG = {
    "cn": "zh", "tw": "zh", "hk": "zh", "ru": "ru", "il": "he", "jp": "ja", "kr": "ko",
    "de": "de", "at": "de", "ch": "de", "fr": "fr", "es": "es", "it": "it", "br": "pt",
    "pt": "pt", "pl": "pl", "nl": "nl", "tr": "tr", "ua": "uk", "cz": "cs", "se": "sv",
}

# the article extractor is trusted unless it keeps less than this share of a
# page's visible text. measured: articles, docs and a forum thread kept 53 to
# 99 percent (vinilbox, headfonics, wikipedia, docs.python.org,
# audiosciencereview); rfc 9110 kept 1 percent, a winehq bugzilla page 3
# percent (the menu, not the bug). falling back costs some menu noise around
# the content; not falling back loses the content, so the floor is low.
FULLTEXT_MIN_CHARS = 1000
FULLTEXT_RATIO = 0.2

# a bot wall is a short page: reddit's had 124 characters of text, winehq's
# anubis page about 1,100, cloudflare's interstitial a few hundred. a longer
# page that says "just a moment" is an article. the weak markers ("captcha",
# "access denied") count only in the text of a page of a few lines, never in
# its source: a page may load a captcha script for its comment form.
WALL_MAX_CHARS = 2000
WEAK_WALL_MAX_CHARS = 600

# verification interstitials seen on frontend instances and imdb. too
# ordinary for a search results page, decisive on a page this short.
_WALL_PHRASES = (
    "checking you are not a bot", "verification process may take",
    "verify that you're not a robot", "verify that you are not a robot",
)
# walls that count only in a short page's visible text, never in its
# source: an embed's script may carry the phrase for its own fallback.
# ncbi's cookie check (http 203) sets a cookie by script and reloads
_WALL_TEXT_PHRASES = ("cookies must be enabled",)

# less article text than this from a big html body means the text is built
# by javascript (smzdm: 349 characters of buttons out of 180 kb of html)
THIN_CHARS = 500
THIN_HTML_BYTES = 20000

PDF_MAX_PAGES = 300

# part of every cached page's key; raise it when the reply document changes,
# so pages cached by an older version are read again. 3: models.Page.
DOC_VERSION = 3

# route racing (see _race): the next route starts when one fails, or when
# the running ones have taken this long without an answer; at most this
# many run at once. a dead frontend fails in a second or two, a slow one
# holds a route for TIMEOUT_S, and a toll can take several seconds.
HEDGE_AFTER_S = 6.0
MAX_PARALLEL = 2

# focus (see _focus): the text is cut into passages of about this many
# characters, whole lines where the lines allow it
FOCUS_PASSAGE_CHARS = 600


def guess_lang(url):
    host = url.split("/")[2].lower() if "://" in url else ""
    tld = host.rsplit(".", 1)[-1] if "." in host else ""
    return _TLD_LANG.get(tld, "en")


def readable(ctype):
    """whether a body of this content type is worth downloading: html, xml,
    text, json, pdf, or undeclared. images, audio, video, archives are not."""
    return is_text(ctype) or is_html(ctype, b"") or "pdf" in ctype


def _extract(html, url):
    """(text, meta, notes, visible): the article when the extractor found a
    sound one, otherwise the page's visible text, and a note saying which."""
    doc = trafilatura.bare_extraction(
        html, url=url, include_comments=False, include_tables=True, include_links=False,
        favor_recall=True, with_metadata=True,
        # the exhaustive date search took 9 of the 11 seconds on rfc 9110
        date_extraction_params={"extensive_search": False, "original_date": True},
    )
    article = tidy(doc.text or "") if doc is not None else ""
    meta = head_meta(html)
    if doc is not None:
        for key in ("title", "author", "date", "sitename", "description"):
            meta[key] = getattr(doc, key, None) or meta.get(key, "")
    visible = visible_text(html)
    if not article:
        return visible, meta, ["no article body found; this is the page's visible text"], visible
    if len(visible) > FULLTEXT_MIN_CHARS and len(article) < FULLTEXT_RATIO * len(visible):
        note = f"the article extractor kept {len(article)} of {len(visible)} characters; this is the page's full visible text"
        return visible, meta, [note], visible
    return article, meta, [], visible


def _wall(html, visible):
    """the refusal phrase that makes a short page a bot wall, or None."""
    if len(visible) > WALL_MAX_CHARS:
        return None
    head, text = html[:20000].lower(), visible.lower().replace("’", "'")
    strong = (next((m for m in net.STRONG_MARKERS + _WALL_PHRASES if m in head or m in text), None)
              or next((m for m in _WALL_TEXT_PHRASES if m in text), None))
    if strong or len(visible) > WEAK_WALL_MAX_CHARS:
        return strong
    return next((m for m in net.WEAK_MARKERS if m in text), None)


def _pdf_text(body):
    """(text, title, notes)."""
    reader = PdfReader(io.BytesIO(body))
    if reader.is_encrypted and not reader.decrypt(""):
        # most "encrypted" pdfs only restrict printing and open with an empty password
        raise FileNotDecryptedError("the pdf needs a password")
    parts, unreadable = [], 0
    for page in reader.pages[:PDF_MAX_PAGES]:
        try:
            parts.append(page.extract_text() or "")
        except Exception as e:  # noqa: BLE001 - one unreadable page should not lose the document
            parts.append(f"[page unreadable: {type(e).__name__}]")
            unreadable += 1
    total = len(reader.pages)
    notes = [
        f"read the first {PDF_MAX_PAGES} of {total} pages" if total > PDF_MAX_PAGES else "",
        f"{unreadable} of {min(total, PDF_MAX_PAGES)} pages could not be read" if unreadable else "",
    ]
    meta = reader.metadata or {}
    return "\n\n".join(parts).strip(), str(meta.get("/Title") or "").strip(), [n for n in notes if n]


def http_error(status, retry_after=None, marker=None):
    """the error text of an http status, saying when a retry can help."""
    text = f"http {status}" + (f" ({marker})" if marker and marker != f"http {status}" else "")
    if status == 429 or status >= 500:
        wait = f"retry in {retry_after}s" if retry_after is not None else "worth a retry later"
        return f"{text}: the server is busy or failing, {wait}"
    return text


def _document(url, final, status, content_type, body, cut, raw, short=False, retry_after=None):
    """a loaded page as a models.Page. cpu-bound (extraction, pdf parsing),
    so it runs in a thread. `short`: the page is short by design (an embed),
    so little text is not a sign of a javascript page."""
    ctype = content_type.split(";")[0].strip().lower()
    base = {"url": url, "via": final if final != url else "", "status": status, "content_type": ctype,
            "partial": net.slow_cut(cut)}
    if status >= 400:
        marker = net.challenge_marker(status, final, body[:20000].decode("utf-8", "replace"))
        return Page(**base, error=http_error(status, retry_after, marker))
    if not readable(ctype):
        return Page(**base, error=f"not a text document ({ctype}); fetch reads html, text and pdf")
    notes = [cut] if cut else []
    if "pdf" in ctype or body[:5] == b"%PDF-":
        if cut:
            return Page(**base, error=f"the pdf is incomplete ({cut}); a cut pdf cannot be read")
        try:
            text, title, pdf_notes = _pdf_text(body)
        # pypdf reports a malformed file with its own errors and, deeper in
        # the parser, with plain ones (IndexError, zlib.error, ...); whichever
        # it is, the file cannot be read, and the reply says so
        except Exception as e:  # noqa: BLE001
            return Page(**base, error=f"pdf unreadable: {type(e).__name__}: {e}")
        if not text.strip():
            return Page(**base, title=title, error="the pdf has no text layer (a scan?)")
        return Page(**base, kind="pdf", title=title, notes=notes + pdf_notes, text=text)
    if not is_html(ctype, body):
        text = decode_html(body, content_type)
        if not text.strip():
            return Page(**base, error="the document is empty")
        return Page(**base, kind="text", notes=notes, text=text)
    html = decode_html(body, content_type)
    if raw:
        text, visible, meta, kind = html, visible_text(html), head_meta(html), "raw"
    else:
        text, meta, extract_notes, visible = _extract(html, final)
        notes += extract_notes
        kind = "article"
    meta = {k: meta.get(k) or "" for k in ("title", "author", "date", "sitename", "description")}
    wall = _wall(html, visible)
    if wall:
        return Page(**base, **meta, error=f"blocked: the page is a bot wall ({wall!r}), not the content")
    if not text.strip():
        # an app shell or a script-only bot check (reddit served one: a form
        # its own script submits). an empty document cached for a day would
        # hide the page from every later try.
        return Page(**base, **meta, error="no readable text: the page is built by javascript, or is a javascript bot check")
    if kind == "article" and not short and len(text) < THIN_CHARS and len(body) > THIN_HTML_BYTES:
        notes.append(f"only {len(text)} characters of text in {len(body)} bytes of html: the page is built by javascript; the description may carry its gist")
    return Page(**base, **meta, kind=kind, notes=notes, text=text)


async def _load(rt, url, lang, raw, short=False):
    """one page, loaded directly, paying an anubis toll once if it stands in
    the way. always a Page; failures are in its `error`."""
    http, config = rt.http, rt.config
    bucket = f"fetch:{(urlsplit(url).hostname or '').lower()}"

    async def load(spaced=True):
        return await http.navigate("fetch", url, lang=lang, bucket=bucket, max_bytes=config.FETCH_MAX_BYTES,
                                   readable=readable, spaced=spaced)

    try:
        resp, body, cut = await load()
        content_type = resp.headers.get("content-type") or ""
        if is_html(content_type.split(";")[0].lower(), body):
            html = decode_html(body, content_type)
            if anubis.detect(html):
                await anubis.pay(http, "fetch", str(resp.url), html, lang)
                resp, body, cut = await load(spaced=False)
                if anubis.detect(decode_html(body, resp.headers.get("content-type") or "")):
                    raise net.Blocked("anubis challenge again right after paying it")
    except net.UnsafeUrl as e:
        return Page(url=url, error=f"refused: {e}")
    except net.Blocked as e:
        return Page(url=url, error=f"blocked: {e}")
    except net.Resting as e:
        return Page(url=url, error=str(e))
    except RequestException as e:
        return Page(url=url, error=network_error(e))
    return await asyncio.to_thread(_document, url, str(resp.url), resp.status_code,
                                   resp.headers.get("content-type") or "", body, cut, raw, short,
                                   net.retry_after_s(resp.headers.get("retry-after")))


def network_error(e):
    """the error text of a network failure (timeout, dns, reset, redirect
    loop): it may pass, so a later retry is worth it."""
    return f"network error, worth a retry later: {net.describe(e)}"


# -- routes -------------------------------------------------------------------

@dataclass
class _Attempt:
    label: str
    run: Callable[[], Awaitable[Page]]
    # (verdict, reason): "ok" wins, "weak" is kept as a last resort, "fail"
    # is recorded in `tried`
    judge: Callable[[Page], tuple[str, str]]
    on_fail: Callable[[Page, str], Awaitable[None]] | None = None


def _judge_answer(page):
    return ("fail", page.error) if page.error else ("ok", "")


async def _api(rt, route, url, lang):
    try:
        return await route.read(rt, url, lang)
    except sites.ApiError as e:
        return Page(url=url, status=e.status, error=str(e))
    except net.Resting as e:
        return Page(url=url, error=str(e))
    # a route that goes through an engine's session (amazon) meets that
    # engine's refusals; they are its answer, not a weltsuche bug
    except net.Blocked as e:
        return Page(url=url, error=f"blocked: {e}")
    except net.RateLimited as e:
        wait = f"retry in {e.retry_after}s" if e.retry_after is not None else "worth a retry later"
        return Page(url=url, error=f"{e}: {wait}")
    except net.Rejected as e:
        return Page(url=url, error=str(e))
    except RequestException as e:
        return Page(url=url, error=network_error(e))


async def _through(rt, url, target, lang, raw, note, short=False):
    """`target` loaded in place of `url`: cited as `url`, read `via` target."""
    page = await _load(rt, target, lang, raw, short)
    if page.error:
        return page
    return page.model_copy(update={"url": url, "via": page.via or target, "notes": [note, *page.notes]})


async def _attempts(rt, site, url, lang, raw):
    """the site's routes for this url as attempts, in order, and notes about
    routes that have nothing to try (an instance list that failed to load)."""
    parts = urlsplit(url)
    out, notes = [], []
    for route in sites.applicable(site, url):
        if isinstance(route, sites.Api):
            if not raw:
                out.append(_Attempt(route.label, lambda r=route: _api(rt, r, url, lang), _judge_answer))
        elif isinstance(route, sites.Load):
            try:
                target = route.url(parts)
            except sites.ApiError as e:
                notes.append(f"{route.label}: {e}")
                continue
            if target == url:
                out.append(_Attempt(route.label, lambda: _load(rt, url, lang, raw), _judge_load(route)))
            else:
                out.append(_Attempt(route.label, lambda t=target, r=route: _through(rt, url, t, lang, raw, f"read through {r.label}", r.short),
                                    _judge_load(route)))
        else:
            try:
                ready, note = await frontends.instances(rt, route.key)
            except RequestException as e:
                ready, note = [], f"the {route.key} instance list could not be loaded ({net.describe(e)})"
            notes.append(note)
            for instance in ready[:frontends.MAX_TRIES]:
                host = urlsplit(instance).hostname
                target = instance + route.path(parts)
                out.append(_Attempt(
                    f"{route.key} {host}",
                    lambda t=target, h=host, k=route.key: _through(rt, url, t, lang, raw, f"read through {h}, a public {k} frontend for {site.name}"),
                    _judge_frontend(site, url, raw),
                    on_fail=lambda page, reason, i=instance: _park(rt, i, page, reason),
                ))
    return out, [n for n in notes if n]


def _judge_load(route):
    def judge(page):
        if page.error:
            return "fail", page.error
        if route.thin_is_weak and len(page.text) < THIN_CHARS:
            return "weak", f"thin: {len(page.text)} characters"
        return "ok", ""
    return judge


def _judge_frontend(site, url, raw):
    tokens = frontends.expected_tokens(url) if site.words_in_text and not raw else set()

    def judge(page):
        if page.error:
            return "fail", page.error
        if not frontends.relevant(page, tokens):
            return "fail", "an unrelated page (the instance answers every path with the same page)"
        return "ok", ""
    return judge


async def _park(rt, instance, page, reason):
    if page.error and frontends.park_reason(page) is None:
        return
    await frontends.park(rt, instance, reason)


async def _race(url, attempts):
    """run attempts in order: the next one starts when one fails, or when the
    running ones took HEDGE_AFTER_S without an answer (at most MAX_PARALLEL
    at once). the first good answer wins and the rest are cancelled.
    returns (page or None, failures as "label: reason", weak fallback)."""
    queue = list(attempts)
    running = {}
    tried, weak = [], None

    def start():
        if queue:
            attempt = queue.pop(0)
            running[asyncio.create_task(attempt.run())] = attempt

    start()
    try:
        while running:
            done, _ = await asyncio.wait(running, timeout=HEDGE_AFTER_S, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                if len(running) < MAX_PARALLEL:
                    start()
                continue
            for task in done:
                attempt = running.pop(task)
                try:
                    page = task.result()
                except Exception as e:  # a bug in one route must not lose the others; logged with its traceback
                    log.exception("fetch: route %s failed for %s", attempt.label, url)
                    page = Page(url=url, error=f"internal {type(e).__name__}: {e} (a weltsuche bug; the traceback is in weltsuche.log in the data folder)")
                verdict, reason = attempt.judge(page)
                if verdict == "ok":
                    return page, tried, weak
                tried.append(f"{attempt.label}: {reason}")
                if verdict == "weak":
                    weak = weak or page
                elif attempt.on_fail:
                    await attempt.on_fail(page, reason)
                start()
        return None, tried, weak
    finally:
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)


async def _read(rt, url, lang, raw):
    """the page, by whichever route reads it."""
    try:
        # before the site routes, which mostly build their own api urls and
        # so skip the direct load's vetting: none may start from a url that
        # vetting refuses (file://airbnb.x/rooms/1, found in review 2026-10-03)
        net.check_scheme(url)
    except net.UnsafeUrl as e:
        return Page(url=url, error=f"refused: {e}")
    site = sites.match(url)
    if site is None:
        return await _load(rt, url, lang, raw)
    attempts, notes = await _attempts(rt, site, url, lang, raw)
    if not attempts:
        page = await _load(rt, url, lang, raw)
        return page.with_note(f"no {site.name} route reads this kind of page; read directly", *notes)
    page, tried, weak = await _race(url, attempts)
    if page is not None:
        return page.model_copy(update={"tried": tried, "notes": [*page.notes, *notes]})
    if weak is not None:
        # a thin answer for want of a better one: not cached, a later try
        # may find a route that reads the whole page
        return weak.model_copy(update={"tried": tried, "partial": True,
                                       "notes": [*weak.notes, "no other route did better", *notes]})
    log.info("fetch: %s: every %s route failed: %s", url, site.name, "; ".join(tried))
    return Page(url=url, error=f"{site.name}: no route could read it (see tried)", tried=tried, notes=notes)


def _passages(text):
    """(offset, passage) pieces of `text` of about FOCUS_PASSAGE_CHARS, cut
    at line ends; a longer line (a pdf paragraph, a minified page) is cut at
    spaces. one pass with an index: re-slicing the rest of a long line at
    every cut took 15 s for one line of 8 million characters."""
    out, start, end = [], 0, 0
    for line in text.splitlines(keepends=True):
        pos = 0
        while len(line) - pos > FOCUS_PASSAGE_CHARS:
            space = line.rfind(" ", pos, pos + FOCUS_PASSAGE_CHARS)
            cut = space + 1 if space > pos else pos + FOCUS_PASSAGE_CHARS
            if end > start:
                out.append((start, text[start:end]))
            out.append((end, line[pos:cut]))
            end += cut - pos
            start, pos = end, cut
        rest = len(line) - pos
        if end - start + rest > FOCUS_PASSAGE_CHARS and end > start:
            out.append((start, text[start:end]))
            start = end
        end += rest
    if end > start:
        out.append((start, text[start:end]))
    return out


def _focus(text, focus, max_chars):
    """(the passages of `text` that mention the most words of `focus`, in
    page order and within max_chars, each opened with its offset; whether
    matching passages were left out; a note), or (None, False, note) when no
    passage matches. a long page or a pdf costs its relevant part, not its
    first slice."""
    tokens = query_tokens(focus)
    if not tokens:
        return None, False, f"focus {focus!r} has no word of three letters or more; the page follows as without focus"
    scored = []
    for offset, passage in _passages(text):
        low = passage.lower()
        hits = [t for t in tokens if t in low]
        if hits:
            scored.append((len(hits), sum(low.count(t) for t in hits), offset, passage))
    if not scored:
        return None, False, f"focus: no passage mentions {focus!r}; the page follows as without focus"
    ranked = sorted(scored, key=lambda s: (-s[0], -s[1], s[2]))
    picked, size = [], 0
    for _, _, offset, passage in ranked:
        piece = f"[at {offset}] {passage.strip()}"
        if not picked and len(piece) > max_chars:
            # the best passage does not fit: its start, rather than weaker
            # passages that do
            picked = [(offset, piece[:max_chars])]
            break
        if size + len(piece) <= max_chars:
            picked.append((offset, piece))
            size += len(piece) + 2
    note = (f"focus {focus!r}: {len(picked)} of {len(scored)} matching passages; [at N] is where one starts, "
            f"fetch again without focus and with offset=N to read on from there")
    return "\n\n".join(p for _, p in sorted(picked)), len(picked) < len(scored), note


async def _cached(cache, key, ttl):
    raw = await cache.get(key, ttl)
    if raw is None:
        return None
    try:
        return Page.model_validate(raw)
    except ValidationError as e:
        log.warning("fetch: cached page %s does not fit the model (%s); reading again", key[:80], e.errors()[:1])
        return None


async def fetch(rt, url, lang=None, max_chars=None, offset=0, raw=False, focus=None):
    """one slice of a page's text as a FetchReply, or with `focus` the
    passages that mention it. never raises: a failure, including a bug in
    weltsuche, comes back in `error` and in the log."""
    config = rt.config
    lang = (lang or guess_lang(url)).strip().lower().replace("_", "-").split("-")[0][:3] or "en"
    max_chars = max(200, int(max_chars or config.FETCH_MAX_CHARS))
    offset = max(0, int(offset or 0))
    key = f"fetch|v{DOC_VERSION}|{int(raw)}|{lang}|{url}"

    page = await _cached(rt.cache, key, config.CACHE_TTL_S)
    from_cache = page is not None
    if page is None:
        try:
            page = await _read(rt, url, lang, raw)
        except Exception as e:  # the tool reply names the failure; the log keeps the traceback
            log.exception("fetch: %s", url)
            page = Page(url=url, error=f"internal {type(e).__name__}: {e} (a weltsuche bug; the traceback is in weltsuche.log in the data folder)")
        if page.error:
            log.info("fetch: %s: %s", url, page.error)
        elif not page.partial:
            # not cached when it failed or came back incomplete for a reason
            # that may pass (a slow download, a failed side request): a
            # refusal, a wall or a network error is worth trying again later,
            # and a degraded page kept for a day would hide the whole one
            await rt.cache.put(key, page.model_dump())

    text = page.text
    if focus and text:
        # cpu-bound on a long page: off the event loop, so the other tool
        # calls of the session go on meanwhile
        piece, truncated, note = await asyncio.to_thread(_focus, text, focus, max_chars)
        page = page.with_note(note)
        if piece is not None:
            return FetchReply(**{**page.model_dump(), "text": piece}, chars_total=len(text), offset=0,
                              chars_returned=len(piece), truncated=truncated, cached=from_cache)
    piece = text[offset: offset + max_chars]
    end = offset + len(piece)
    return FetchReply(
        **{**page.model_dump(), "text": piece},
        chars_total=len(text), offset=offset, chars_returned=len(piece),
        truncated=end < len(text), next_offset=end if end < len(text) else None, cached=from_cache,
    )


# the page text one fetch_many call returns by default: four pages of the
# default FETCH_MAX_CHARS. the tool exists because a tool shaped as a list
# gets batched and an instruction to call a single-url tool repeatedly does
# not (found in review 2026-10-05: every past fetch call across real sessions
# asked for one url, never several in one tool call, despite the skill
# telling the model to send them together; search_many, which takes a list
# directly, was always used correctly)
FETCH_MANY_BUDGET_CHARS = 48000

# the longest fetch_many reply claude code shows inline; server.py declares
# it on the tool. the budget above counts page text only: each page's
# envelope (url, title, notes: about 400 characters) and the json escaping
# come on top, here with room for about 40 pages. without the declaration
# claude code saves a tool's reply to a file once it is longer than 50,000
# characters, whatever its token count, and shows the model a 2 kb preview
# instead. measured 2026-10-05 with long wikipedia pages: 49,652 characters
# for 4 urls, 51,179 for 8, and of those 8 pages none arrived. the 25,000
# tokens of MAX_MCP_OUTPUT_TOKENS were not what cut it: 48,000 characters of
# chinese came through inline
FETCH_MANY_REPLY_MAX_CHARS = 64000


async def fetch_many(rt, requests, concurrency=3):
    """requests: a list of models.FetchRequest objects (or anything with the
    same attributes). runs `concurrency` at a time; a request without its own
    `max_chars` gets a share of FETCH_MANY_BUDGET_CHARS, never more than
    FETCH_MAX_CHARS, so asking for more urls at once returns less of each
    rather than risking the whole reply."""
    # capped at a single fetch's default: the bare share gave one url 48,000
    # characters and two urls 24,000 each, more than the same pages cost
    # fetched one by one, and passed over a configured FETCH_MAX_CHARS
    # (found in review 2026-10-05)
    share = min(rt.config.FETCH_MAX_CHARS, max(1, FETCH_MANY_BUDGET_CHARS // max(1, len(requests))))
    sem = asyncio.Semaphore(concurrency)

    async def one(r):
        async with sem:
            try:
                return await fetch(rt, r.url, r.lang, r.max_chars or share, r.offset, r.raw, r.focus)
            except Exception as e:  # one url's failure must not lose the others' pages; logged with its traceback
                log.exception("fetch_many: %s", r.url)
                return FetchReply(url=r.url, error=f"internal {type(e).__name__}: {e} (a weltsuche bug; the traceback is in weltsuche.log in the data folder)",
                                  chars_total=0, offset=0, chars_returned=0, truncated=False)

    return await asyncio.gather(*(one(r) for r in requests))
