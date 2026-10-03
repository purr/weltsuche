"""http that looks like a person at a browser.

one curl_cffi session per engine (and one for page fetches), each impersonating
the same chrome build with a persistent cookie jar, an accept-language that
matches the language being searched, and a referer that says the request came
from the engine's own page. requests to one bucket (an engine, or one host for
page fetches) are spaced and jittered.

what this deliberately does not do: rotate fingerprints or proxies, retry into
a block, or solve captchas. a 429 or a challenge page is a signal to stop for a
while, and the breaker in `state.py` does exactly that.
"""

import asyncio
import email.utils
import ipaddress
import logging
import random
import re
import socket
import time
from contextlib import asynccontextmanager, nullcontext
from http.cookiejar import Cookie
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from curl_cffi import CurlOpt
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import TooManyRedirects

from . import state

log = logging.getLogger("weltsuche.net")

ACCEPT_LANGUAGE = {
    "en": "en-US,en;q=0.9",
    "de": "de-DE,de;q=0.9,en-US;q=0.7,en;q=0.6",
    "zh": "zh-CN,zh;q=0.9,en;q=0.6",
    "ru": "ru-RU,ru;q=0.9,en;q=0.6",
    "he": "he-IL,he;q=0.9,en;q=0.6",
    "ja": "ja-JP,ja;q=0.9,en;q=0.6",
    "ko": "ko-KR,ko;q=0.9,en;q=0.6",
    "fr": "fr-FR,fr;q=0.9,en;q=0.6",
    "es": "es-ES,es;q=0.9,en;q=0.6",
    "it": "it-IT,it;q=0.9,en;q=0.6",
    "pt": "pt-BR,pt;q=0.9,en;q=0.6",
    "ar": "ar-SA,ar;q=0.9,en;q=0.6",
    "tr": "tr-TR,tr;q=0.9,en;q=0.6",
    "pl": "pl-PL,pl;q=0.9,en;q=0.6",
    "nl": "nl-NL,nl;q=0.9,en;q=0.6",
    "uk": "uk-UA,uk;q=0.9,ru;q=0.7,en;q=0.6",
}

# phrases that mean "you are a bot, go away", lower-cased. a page containing
# one of these is a block, not a result, and the engine gets a long rest.
# STRONG markers never appear on a genuine results page, so they count at any
# page size. WEAK ones ("captcha" is a perfectly good search term) count only
# when the page is small, which a challenge page always is.
STRONG_MARKERS = (
    'id="anubis_challenge"', "/.within.website/x/cmd/anubis/", "verifying your request", "just a moment",
    "cf-chl", "cf_chl", "unusual traffic", "are you a robot", "verify you are human",
    "wappass.baidu.com", "百度安全验证", "smartcaptcha", "checking your browser",
    "please enable javascript and cookies to continue", "prove your humanity",
    # amazon: the automated-access page (http 503) and the "continue
    # shopping" interstitial it serves a session it does not know yet
    "api-services-support@amazon.com", "click the button below to continue shopping",
    "klicke auf die schaltfläche unten, um mit dem einkauf fortzufahren",
)
WEAK_MARKERS = ("captcha", "access denied")
# script and style contents, dropped before the weak markers are looked for
_SCRIPTS = re.compile(r"<(script|style)\b.*?</\1\s*>", re.S)

# a browser follows this many redirects before giving up; so does `navigate`
MAX_REDIRECTS = 8

# a Set-Cookie Expires is an absolute time on the server's clock. chrome
# shifts it by the difference between the server's clock (the Date header)
# and the local one; curl does not, so when the local clock runs ahead, a
# cookie meant to live 30 minutes arrives already expired and curl drops it.
# seen 2026-09-29: with a local clock an hour ahead, startpage's anubis
# verification cookie vanished on arrival, and every toll payment failed with
# "cookies appear to be disabled" (http 500), parking startpage for an hour.
SKEW_TOLERANCE_S = 60


class Blocked(Exception):
    """the engine served a challenge or refused us. rest for BREAKER_BLOCK_S."""


class RateLimited(Exception):
    """429 or an equivalent. rest for retry-after or BREAKER_RATE_LIMIT_S."""

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class Unavailable(RateLimited):
    """a server error (5xx): says nothing about this client. a short rest,
    as the server asks (Retry-After) or for BREAKER_RATE_LIMIT_S."""


class Rejected(Exception):
    """a 4xx other than 403 and 429 (400, 404, 414, 422, ...): the request,
    usually the query, was refused. no rest: the next query may be fine."""


class Resting(Exception):
    """the bucket's breaker opened while this request waited for its slot (a
    sibling request of the same search was refused). not a new failure: the
    request is not sent into a block that is already known."""


class UnsafeUrl(Exception):
    """the url leads off the public web: another scheme, this machine, the
    local network. a fetched page can ask the model to fetch something, and
    `file://` or `http://192.168.1.1/` must not be something it can ask for."""


def accept_language(lang):
    return ACCEPT_LANGUAGE.get(lang, f"{lang},en;q=0.7")


_CURL_NOISE = re.compile(r"^Failed to perform, |\s*See https://curl\.se/\S+ first for more details\.?")


def describe(e):
    """an exception as one short line for a reply: curl_cffi's boilerplate
    ("Failed to perform, ... See https://curl.se/... for more details") is
    dropped, it was most of every network error the model was shown."""
    return f"{type(e).__name__}: {_CURL_NOISE.sub('', str(e))}"


def retry_after_s(value):
    """seconds a Retry-After header asks to wait (delay-seconds or an
    http-date), None when there is none or it cannot be read."""
    value = (value or "").strip()
    if value.isdigit():
        return int(value)
    if not value:
        return None
    try:
        return max(0, int(email.utils.parsedate_to_datetime(value).timestamp() - time.time()))
    except (TypeError, ValueError):
        return None  # an unreadable date: the caller's own rest applies


def challenge_marker(status, url, text, query=""):
    """the marker that makes this page a refusal, or None for a real page.
    pure, so a saved page can be tested without a response object.

    a phrase the query itself contains is skipped: a search for "captcha
    solver" returns a page full of the word captcha because it quotes the
    query back, and parking the engine an hour for that would be wrong.
    """
    url = (url or "").lower()
    # yandex's captcha is a redirect to /showcaptcha, so the url is checked;
    # the word is not a text marker: every mediawiki page (wikipedia,
    # wiktionary) carries "wgConfirmEditForceShowCaptcha" in its config, and
    # a short one (a stub, a redirect page) was blocked as a bot wall for it
    # (found 2026-10-05)
    if "showcaptcha" in url or "wappass.baidu.com" in url or "/sorry/" in url:
        return "challenge redirect"
    if status == 403:
        return "http 403"
    head = (text or "")[:20000].lower()
    q = (query or "").lower()
    for marker in STRONG_MARKERS:
        if marker in head and marker not in q:
            return marker
    if len(head) < 12000:
        # weak markers are words a wall shows. a script's config or a captcha
        # widget's site key is not a wall: "wgConfirmEditForceShowCaptcha"
        # sits in every mediawiki page's config (found 2026-10-05)
        shown = _SCRIPTS.sub(" ", head)
        for marker in WEAK_MARKERS:
            if marker in shown and marker not in q:
                return marker
    return None


def classify(resp, query="", markers=True):
    """raise Blocked, RateLimited, Unavailable or Rejected when a response is
    not a real answer. `markers=False` judges by status and url only: a
    results page that parsed into hits is an answer, whatever phrase a
    snippet on it contains.

    only a challenge, a captcha or a 403 is a block (an hour's rest). a 5xx
    is the server's trouble and a 4xx the request's: both used to park the
    engine for an hour as "blocked", which told the model a passing fault
    was a refusal (found in review 2026-10-03)."""
    status = resp.status_code
    if status == 429:
        raise RateLimited(f"http 429 from {resp.url}", retry_after_s(resp.headers.get("retry-after")))
    if status == 202:
        # duckduckgo answers 202 with an "anomaly" page when it wants a pause
        raise RateLimited(f"http 202 (anomaly page) from {resp.url}")
    # an error page is no results page: its text is judged whatever
    # `markers` says (amazon's 503 is a bot wall, not a server fault)
    marker = challenge_marker(status, str(resp.url), resp.text if markers or status >= 400 else "", query)
    if marker:
        raise Blocked(f"challenge page from {resp.url} ({marker})")
    if status >= 500:
        raise Unavailable(f"http {status} from {resp.url}", retry_after_s(resp.headers.get("retry-after")))
    if status >= 400:
        raise Rejected(f"http {status} from {resp.url}")


def clock_skew(resp):
    """local clock minus the server's, in seconds; 0 without a usable Date."""
    date = resp.headers.get("date")
    if not date:
        return 0.0
    try:
        return time.time() - email.utils.parsedate_to_datetime(date).timestamp()
    except (TypeError, ValueError):
        # a malformed Date header: no correction, which is what curl does anyway
        return 0.0


def restore_skewed_cookies(jar, resp):
    """put back the cookies curl dropped as expired only because the local
    clock disagrees with the server's, with their lifetime counted from now.
    returns how many were restored. see SKEW_TOLERANCE_S."""
    skew = clock_skew(resp)
    if abs(skew) < SKEW_TOLERANCE_S:
        return 0
    now = time.time()
    host = (urlsplit(str(resp.url)).hostname or "").lower()
    restored = 0
    for raw in resp.headers.get_list("set-cookie"):
        morsels = SimpleCookie()
        try:
            morsels.load(raw)
        except CookieError:
            # curl_cffi's own parser skips such a header the same way; curl
            # did not store it either, so there is nothing to restore
            continue
        for name, m in morsels.items():
            if m["max-age"] or not m["expires"]:
                continue  # a relative lifetime or a session cookie needs no correction
            try:
                expires = email.utils.parsedate_to_datetime(m["expires"]).timestamp() + skew
            except (TypeError, ValueError):
                continue  # an unreadable date: curl kept or dropped it on its own terms
            if expires <= now:
                continue  # expired on the server's clock too: a deletion, honour it
            domain = m["domain"].lower()
            domain = "." + domain.lstrip(".") if domain else host
            jar.set_cookie(Cookie(
                version=0, name=name, value=m.value, port=None, port_specified=False,
                domain=domain, domain_specified=bool(m["domain"]), domain_initial_dot=domain.startswith("."),
                path=m["path"] or "/", path_specified=True, secure=bool(m["secure"]),
                expires=int(expires), discard=False, comment=None, comment_url=None, rest={},
            ))
            restored += 1
    if restored:
        log.debug("cookies: restored %d from %s, local clock %+.0fs off the server's", restored, host, skew)
    return restored


def vet_ip(url, ip):
    """raise UnsafeUrl unless `ip` is a public address."""
    if not ip:
        raise UnsafeUrl(f"{url}: no address to check")
    addr = ipaddress.ip_address(ip.split("%")[0])
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if not addr.is_global or addr.is_multicast:
        kind = "loopback" if addr.is_loopback else "private or reserved"
        raise UnsafeUrl(f"{url}: {ip} is a {kind} address; fetch reads the public web only")


def check_scheme(url):
    """UnsafeUrl unless `url` is http or https."""
    scheme = urlsplit(url).scheme
    if scheme not in ("http", "https"):
        raise UnsafeUrl(f"{url}: only http and https are fetched, not {scheme or 'a url without a scheme'}")


async def vet_url(url):
    """(host, port, vetted addresses) for an http(s) url whose host resolves
    to public addresses only; UnsafeUrl otherwise. no addresses when dns
    failed here: curl then resolves on its own and reports the failure, and
    should it get an answer after all, vet_ip checks what it connected to."""
    parts = urlsplit(url)
    check_scheme(url)
    host = parts.hostname
    if not host:
        raise UnsafeUrl(f"{url}: no host")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as e:
        raise UnsafeUrl(f"{url}: {e}") from e
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return host, port, []
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    for address in addresses:
        vet_ip(url, address)
    return host, port, addresses


def _resolve_entry(host, port, addresses):
    """a curl RESOLVE line: host:port:addr[,addr], ipv6 in brackets. None for
    a host that is an address itself: there is no dns to rebind, and curl
    cannot parse an ipv6 host in that place; one such line made every later
    request of the session fail with curl error 49 (found in review
    2026-10-03)."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return f"{host}:{port}:" + ",".join(f"[{a}]" if ":" in a else a for a in addresses)
    return None


async def _read(resp, max_bytes, timeout):
    """the body of a streamed response and why it was cut ("" when whole).

    the transfer is stopped (quit_now) once the body passes max_bytes or the
    read runs past `timeout`. without the stop curl keeps downloading into
    memory and aclose() waits for all of it; a radio stream never ends
    (found in review 2026-09-29: still reading 8 s after a 1 mb cut)."""
    chunks, size, cut = [], 0, ""
    try:
        async with asyncio.timeout(timeout):
            async for chunk in resp.aiter_content():
                chunks.append(chunk)
                size += len(chunk)
                if size >= max_bytes:
                    cut = f"cut at {max_bytes} bytes (FETCH_MAX_BYTES)"
                    break
    except TimeoutError:
        cut = f"{_SLOW_CUT} {timeout:.0f}s of downloading (TIMEOUT_S)"
    if cut:
        _stop(resp)
    return b"".join(chunks)[:max_bytes], cut


_SLOW_CUT = "cut after"


def slow_cut(cut):
    """whether a `cut` from navigate means the download ran out of time,
    which the next try may not: such a page is incomplete for a passing
    reason and not cached."""
    return cut.startswith(_SLOW_CUT)


def _stop(resp):
    """tell curl to abort a streamed transfer at its next chunk."""
    if resp.quit_now is not None:
        resp.quit_now.set()


async def _close(resp, url):
    """close a streamed response. a server that sends nothing more never
    reaches the abort, and curl only gives up at its low-speed limit; the
    tool call does not wait for that."""
    try:
        async with asyncio.timeout(5):
            await resp.aclose()
    except TimeoutError:
        log.warning("net: %s did not close within 5s; curl drops it at its low-speed limit", _short(url))


class Http:
    """sessions, cookies, spacing. one instance per process."""

    def __init__(self, config, breakers, cookie_dir):
        self.config = config
        self.breakers = breakers
        self.cookie_dir = Path(cookie_dir)
        self._sessions = {}
        self._locks = {}
        # (host, port) -> RESOLVE line of the addresses last vetted for it
        self._pins = {}

    def _lock(self, bucket):
        if bucket not in self._locks:
            self._locks[bucket] = asyncio.Lock()
        return self._locks[bucket]

    @asynccontextmanager
    async def _slot(self, bucket, gap=None):
        """hold `bucket` for one page load: claim its next slot across all
        processes, wait for it plus jitter, and go, unless its breaker opened
        while waiting (a queued request of the same search_many must not go
        out into a block its sibling just ran into). `gap` is an api's own
        documented limit, which replaces ENGINE_GAP_S and the jitter: the
        jitter is there to look like a person to web engines, and an api
        with a published limit does not judge timing."""
        async with self._lock(bucket):
            wait = await self.breakers.reserve(bucket, self.config.ENGINE_GAP_S if gap is None else gap)
            jitter = random.uniform(*self.config.JITTER_S) if gap is None else 0.0
            log.debug("net: %s waiting %.1fs", bucket, wait + jitter)
            await asyncio.sleep(wait + jitter)
            is_open, reason, left = await self.breakers.status(bucket)
            if is_open:
                raise Resting(f"resting {left}s: {reason}")
            yield

    def session(self, name):
        """the session `name`, made on first use. its cookies come from the
        cookie file before every request (_sync_cookies)."""
        if name not in self._sessions:
            self._sessions[name] = AsyncSession(
                impersonate=self.config.IMPERSONATE,
                timeout=self.config.TIMEOUT_S,
                allow_redirects=True,
                max_redirects=MAX_REDIRECTS,
            )
        return self._sessions[name]

    # -- cookies ---------------------------------------------------------

    def _cookie_path(self, name):
        return self.cookie_dir / f"{name}.json"

    async def _sync_cookies(self, name):
        """the session with the cookie file merged in, and a snapshot of its
        jar. every weltsuche process (one per claude code session) shares the
        file: it is read before each request and only what that request
        changed is written back (save_cookies). writing whole jars let the
        last writer erase the rest: measured 2026-09-29 with five sessions, a
        paid winehq anubis toll vanished from fetch.json within minutes. the
        file is read in a worker thread; the jar is changed here, on the
        loop, where the session is used.

        the read waits for this process's own saves of the file (one lock per
        session), and a cookie the jar changed while the file was read keeps
        the jar's value: another request of the session may have finished
        meanwhile, and the older file brought back a cookie its server had
        just deleted (found in review 2026-10-03)."""
        s = self.session(name)
        async with self._lock(f"cookies:{name}"):
            held = _snapshot(s.cookies.jar)
            stored = await asyncio.to_thread(self._read_cookies, name)
            now = _snapshot(s.cookies.jar)
            moved = {k for k in held.keys() | now.keys() if held.get(k) != now.get(k)}
            self._apply_cookies(name, s, stored, skip=moved)
        return s, _snapshot(s.cookies.jar)

    def _read_cookies(self, name):
        """the cookies stored for `name`; none when the file is missing or
        cannot be read."""
        path = self._cookie_path(name)
        try:
            with state.file_lock(path):
                obj = state.read_json(path, 1)
        except (state.StateUnavailable, OSError) as e:
            # a session without the stored cookies still works, it may just
            # pay a toll again; worth knowing, not worth failing the request
            log.warning("cookies: cannot read %s (%s); going ahead with the session's own", path.name, e)
            return []
        return (obj or {}).get("cookies", [])

    def _apply_cookies(self, name, s, stored, skip):
        """put the stored cookies into the session's jar, except those whose
        (domain, path, name) is in `skip`."""
        n = 0
        for c in stored:
            try:
                if c.get("expires") and c["expires"] < time.time():
                    continue
                if (c.get("domain", ""), c.get("path", "/"), c["name"]) in skip:
                    continue
                # curl names a domain cookie with a leading dot and a host-only
                # one without, and curl_cffi hands domain_specified to curl as
                # "include subdomains". set from the mere presence of a domain,
                # every host-only cookie came back as a domain cookie, which a
                # host-only Set-Cookie can no longer replace or delete:
                # startpage's sp_return outlived its deletion and every request
                # redirected in a loop (2026-10-03)
                s.cookies.jar.set_cookie(Cookie(
                    version=0, name=c["name"], value=c["value"], port=None, port_specified=False,
                    domain=c.get("domain", ""), domain_specified=str(c.get("domain", "")).startswith("."),
                    domain_initial_dot=str(c.get("domain", "")).startswith("."),
                    path=c.get("path", "/"), path_specified=True, secure=bool(c.get("secure")),
                    expires=c.get("expires"), discard=False, comment=None, comment_url=None, rest={},
                ))
                n += 1
            except (KeyError, TypeError, ValueError, AttributeError) as e:
                log.warning("cookies: skipping a bad entry in %s.json: %s", name, e)
        log.debug("cookies: %s loaded %d", name, n)

    async def save_cookies(self, name, before):
        """merge into the cookie file what changed in the session's jar since
        the snapshot `before`: cookies set or changed are written, cookies
        the server removed are dropped, everything else in the file (stored by
        other processes meanwhile) is left as it is."""
        s = self._sessions.get(name)
        if s is None:
            return
        async with self._lock(f"cookies:{name}"):
            after = _snapshot(s.cookies.jar)
            changed = {k: v for k, v in after.items() if before.get(k) != v}
            removed = before.keys() - after.keys()
            if changed or removed:
                await asyncio.to_thread(self._write_cookies, name, changed, removed)

    def _write_cookies(self, name, changed, removed):
        path = self._cookie_path(name)
        try:
            with state.file_lock(path):
                obj = state.read_json(path, 1) or {"cookies": []}
                stored = {(c.get("domain", ""), c.get("path", "/"), c.get("name", "")): c for c in obj.get("cookies", [])}
                for key in removed:
                    stored.pop(key, None)
                for (domain, cpath, cname), (value, expires, secure) in changed.items():
                    stored[(domain, cpath, cname)] = {"name": cname, "value": value, "domain": domain, "path": cpath,
                                                      "expires": expires, "secure": secure}
                now = time.time()
                cookies = [c for c in stored.values() if not c.get("expires") or c["expires"] > now]
                state.write_json_atomic(path, {"version": 1, "cookies": cookies})
        except (state.StateUnavailable, OSError) as e:
            # the response is already here and correct; only the next process
            # misses these cookies
            log.warning("cookies: could not save %s (%s)", path.name, e)

    async def close(self):
        for s in self._sessions.values():
            await s.close()
        self._sessions.clear()

    # -- requests --------------------------------------------------------

    def headers(self, lang, referer=None, *, document=True):
        h = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
            if document else "application/json,text/plain,*/*",
            "Accept-Language": accept_language(lang),
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document" if document else "empty",
            "Sec-Fetch-Mode": "navigate" if document else "cors",
            "Sec-Fetch-Site": "same-origin" if referer else "none",
            "Sec-Fetch-User": "?1",
        }
        if referer:
            h["Referer"] = referer
        return h

    async def request(self, name, method, url, *, lang, referer=None, params=None, data=None, json_body=None,
                      document=True, cookies=None, check=True, allow_redirects=True, extra_headers=None,
                      spaced=True, query="", bucket=None, gap=None):
        """one spaced, jittered request through the session `name`.

        `name` picks the session and cookie jar (an engine, or "fetch");
        `bucket` the spacing, default the same name (an api host gets its
        own, so reading one api does not wait for another); `gap` an api's
        documented spacing instead of ENGINE_GAP_S (see _slot). `spaced=False`
        is for the follow-up requests of one page load (paying a toll, then
        loading the page it guarded), which a browser sends without a pause.
        raises Blocked / RateLimited when `check` is on and the answer is not
        a real page; `query` keeps a page that quotes it from counting as a
        refusal. the caller decides what to do with that; for engines the
        wrapper in `engines/__init__.py` opens the breaker.
        """
        headers = self.headers(lang, referer, document=document)
        if extra_headers:
            headers.update(extra_headers)
        async with (self._slot(bucket or name, gap) if spaced else nullcontext()):
            s, before = await self._sync_cookies(name)
            started = time.monotonic()
            resp = await s.request(method, url, params=params, data=data, json=json_body, headers=headers,
                                   cookies=cookies, allow_redirects=allow_redirects)
            restore_skewed_cookies(s.cookies.jar, resp)
            log.info("net: %s %s %s -> %d in %.1fs (%d bytes)", name, method, _short(str(resp.url)), resp.status_code, time.monotonic() - started, len(resp.content or b""))
            await self.save_cookies(name, before)
        if check:
            classify(resp, query)
        return resp

    async def get(self, name, url, **kw):
        return await self.request(name, "GET", url, **kw)

    async def post(self, name, url, **kw):
        return await self.request(name, "POST", url, **kw)

    async def navigate(self, name, url, *, lang, bucket, max_bytes, readable, spaced=True):
        """load an arbitrary page for `fetch`, the way a browser does.

        redirects are followed here rather than by curl, so every hop is
        vetted: the url before a request goes out (vet_url), and curl is
        pinned to exactly the addresses that passed (RESOLVE), so a dns answer
        that changes between the check and the connection (dns rebinding)
        cannot send the request to a private address; the address curl
        connected to is checked again before any body is read (vet_ip). a
        connection kept alive from an earlier request was vetted when it
        opened. the body is streamed
        and the transfer stopped at max_bytes or after TIMEOUT_S, and not read
        at all when `readable(content_type)` says it is not text. spacing is
        per `bucket` (one per host), not per session: two pages on two sites
        do not wait for each other. returns (response, body, cut), `cut`
        saying why the body is incomplete ("" when whole).
        """
        async with (self._slot(bucket) if spaced else nullcontext()):
            s, before = await self._sync_cookies(name)
            started = time.monotonic()
            try:
                for _ in range(MAX_REDIRECTS + 1):
                    host, port, addresses = await vet_url(url)
                    entry = _resolve_entry(host, port, addresses) if addresses else None
                    if entry:
                        self._pins[(host, port)] = entry
                        # the session applies curl_options to each new request. the
                        # list holds every pin, so a navigation to another host that
                        # sets it meanwhile only adds its own line; navigations to one
                        # host are serialized by its slot. RESOLVE is a curl list
                        # option and takes a list, whatever curl_cffi's str hint says
                        # (verified 2026-09-29: pinned to 1.1.1.1, curl went there)
                        s.curl_options[CurlOpt.RESOLVE] = list(self._pins.values())
                    resp = await s.request("GET", url, headers=self.headers(lang), allow_redirects=False, stream=True)
                    try:
                        vet_ip(url, resp.primary_ip)
                        ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                        if resp.status_code in (301, 302, 303, 307, 308) or readable(ctype):
                            body, cut = await _read(resp, max_bytes, self.config.TIMEOUT_S)
                        else:
                            _stop(resp)
                            body, cut = b"", f"not downloaded: {ctype}"
                    finally:
                        await _close(resp, url)
                    restore_skewed_cookies(s.cookies.jar, resp)
                    location = resp.headers.get("location")
                    if resp.status_code in (301, 302, 303, 307, 308) and location:
                        url = urljoin(url, location)
                        continue
                    log.info("net: %s GET %s -> %d in %.1fs (%d bytes%s)", name, _short(url), resp.status_code,
                             time.monotonic() - started, len(body), f", {cut}" if cut else "")
                    return resp, body, cut
                raise TooManyRedirects(f"more than {MAX_REDIRECTS} redirects, last to {url}")
            finally:
                await self.save_cookies(name, before)

    async def has_cookie(self, name, cookie_name, domain=None):
        """whether the session `name` holds a cookie, the cookie file included;
        with `domain`, one set for that domain or a subdomain of it."""
        s, _ = await self._sync_cookies(name)
        return any(c.name == cookie_name and (domain is None or c.domain.lstrip(".").endswith(domain))
                   for c in s.cookies.jar)


def _snapshot(jar):
    """(domain, path, name) -> (value, expires, secure) for every cookie."""
    return {(c.domain, c.path, c.name): (c.value, c.expires, bool(c.secure)) for c in jar}


def _short(url, n=110):
    url = re.sub(r"[?&](sc|rut|ei|ved|uddg)=[^&]*", "", url)
    return url if len(url) <= n else url[: n - 1] + "…"
