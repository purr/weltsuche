"""the engine registry and the wrapper that runs one engine safely.

an engine module exports:

    NAME        str
    HOME        str, the referer a person would arrive from
    WEIGHT      float, optional, how much a hit counts in the merge (default 1.0)
    BREAKER     str, optional, the engine it rests and is spaced with (default NAME)
    PARAMS      dict, optional, the filters it takes: name -> choices or kind
                (common.check_params); then search() takes them as `params`
    async def search(http, query, lang, region, n[, params]) -> list[dict]

each raw hit is {"title", "url", "snippet"}; `run` turns them into
models.Hit. what the modules share (errors, `settle`, `api_json`) is in
common.py.

besides the modules, config.SITE_ENGINES names site engines: one site
searched through a web engine's index with a `site:` restriction, for sites
whose own search is closed to plain clients (gitee answers anonymous api
searches with nothing). the carrying engine's spacing and breaker apply.
"""

import functools
import logging
import time
from urllib.parse import urlsplit

from curl_cffi.requests.exceptions import RequestException, TooManyRedirects
from pydantic import ValidationError

from .. import net
from ..models import EngineStatus, Hit
from . import (airbnb, amazon, arxiv, baidu, bing, brave, codeberg, crates, csdn, duckduckgo, ebay, github,
               github_code, gitlab, grepapp, hackernews, habr, juejin, kleinanzeigen, naver, naver_blog, npm, pubmed,
               qiita, stackexchange, startpage, v2ex, velog, wikipedia, yandex, zenn)
from .common import NeedsLogin, ParseError, QueryError, check_params, clean_hits, looks_relevant

log = logging.getLogger("weltsuche.engines")

# every engine module by its NAME
MODULES = {m.NAME: m for m in (
    duckduckgo, bing, brave, startpage, baidu, yandex, naver, naver_blog, wikipedia,
    github, github_code, grepapp, codeberg, gitlab, npm, crates,
    hackernews, stackexchange, qiita, zenn, juejin, csdn, v2ex, habr, velog,
    pubmed, arxiv,
    amazon, ebay, kleinanzeigen, airbnb,
)}


def names(config):
    """every engine name: the modules, and the site engines `config` names."""
    return [*MODULES, *config.SITE_ENGINES]


def _on_site(url, domain):
    host = (urlsplit(url).hostname or "").lower()
    return host == domain or host.endswith("." + domain)


async def _site_search(carrier, domain, http, query, lang, region, n):
    """`query` on one site through the carrier's index; hits off the site dropped."""
    hits = await carrier.search(http, f"site:{domain} {query}", lang, region, n)
    return [h for h in hits if _on_site(h.get("url") or "", domain)]


async def _cached(cache, key, ttl):
    raw = await cache.get(key, ttl)
    if raw is None:
        return None
    try:
        return [Hit.model_validate(h) for h in raw]
    except (ValidationError, TypeError) as e:
        log.warning("engines: cached hits for %s do not fit the model (%s); asking again", key[:80], e)
        return None


async def run(name, http, breakers, cache, config, query, lang, region, n, params=None):
    """run one engine with breaker, spacing, cache and classification.
    returns (hits, EngineStatus); the status is what the model is shown."""
    mods = MODULES
    site = config.SITE_ENGINES.get(name)
    if site:
        carrier, domain = site
        if carrier not in mods:
            return [], EngineStatus(engine=name, ok=False, count=0, note=f"SITE_ENGINES names an unknown carrier {carrier!r}")
        ask = functools.partial(_site_search, mods[carrier], domain)
        # a site engine rests and is spaced as its carrier: a block it runs
        # into is the carrier's block
        breaker, via = carrier, f" (via {carrier}, site:{domain})"
    elif name in mods:
        # an engine that shares a site with another (naver_blog) rests with it
        ask, breaker, via = mods[name].search, getattr(mods[name], "BREAKER", name), ""
    else:
        return [], EngineStatus(engine=name, ok=False, count=0, note=f"unknown engine; known: {', '.join(names(config))}")

    allowed = getattr(mods.get(name), "PARAMS", None)
    try:
        used, unused = check_params(name, allowed or {}, params)
    except QueryError as e:
        return [], EngineStatus(engine=name, ok=False, count=0, note=f"the engine rejected the query, rephrase or use another engine: {e}")
    if used:
        ask = functools.partial(ask, params=used)
    # a filter this engine does not take is named, not silently dropped
    unused_note = f"not used by this engine: {', '.join(unused)}" if unused else ""
    key = f"{name}|{lang}|{region}|{n}|{query}|{sorted(used.items())}"
    cached = await _cached(cache, key, config.CACHE_TTL_S)
    if cached is not None:
        return cached, EngineStatus(engine=name, ok=True, count=len(cached), cached=True, note=unused_note)

    def failed(note):
        return [], EngineStatus(engine=name, ok=False, count=0, note=note + via)

    is_open, reason, left = await breakers.status(breaker)
    if is_open:
        return failed(f"resting {left}s: {reason}")

    started = time.monotonic()
    try:
        hits = clean_hits(await ask(http, query, lang, region, n), n)
    except net.Resting as e:
        return failed(str(e))
    except net.RateLimited as e:
        # the server's own number, a Retry-After of 0 included, up to the
        # longest rest weltsuche takes for a block
        seconds = min(e.retry_after if e.retry_after is not None else config.BREAKER_RATE_LIMIT_S, config.BREAKER_BLOCK_S)
        await breakers.open(breaker, seconds, str(e))
        what = "server error" if isinstance(e, net.Unavailable) else "rate limited"
        return failed(f"{what}, resting {seconds}s, retry after that: {e}")
    except net.Blocked as e:
        await breakers.open(breaker, config.BREAKER_BLOCK_S, str(e))
        return failed(f"blocked, resting {config.BREAKER_BLOCK_S}s: {e}")
    except TooManyRedirects as e:
        # an engine that sends a client round in circles is not answering it;
        # asking again at once only repeats the circle (startpage did, after
        # its toll, until the cookie fix in net.Http._apply_cookies)
        await breakers.open(breaker, config.BREAKER_RATE_LIMIT_S, "redirect loop")
        return failed(f"redirect loop, resting {config.BREAKER_RATE_LIMIT_S}s: {net.describe(e)}")
    except (QueryError, net.Rejected) as e:
        log.info("engine %s rejected %r: %s", name, query, e)
        return failed(f"the engine rejected the query, rephrase or use another engine: {e}")
    except NeedsLogin as e:
        return failed(str(e))
    except ParseError as e:
        log.warning("engine %s: %s", name, e)
        return failed(f"parser found nothing usable on a full page, markup may have changed: {e}")
    except RequestException as e:
        # a timeout, a dns failure, a reset: the engine's network or this
        # one's. a short rest keeps a dead engine from adding TIMEOUT_S to
        # every search and is over before a passing fault matters
        await breakers.open(breaker, config.BREAKER_NETWORK_S, net.describe(e))
        return failed(f"network error, resting {config.BREAKER_NETWORK_S}s, retry after that: {net.describe(e)}")
    except Exception as e:  # noqa: BLE001 - one engine failing must not take the search down
        log.warning("engine %s failed for %r", name, query, exc_info=True)
        return failed(f"internal error, a weltsuche bug (the traceback is in weltsuche.log): {net.describe(e)}")

    log.info("engine %s: %d hits in %.1fs for %r [%s/%s]", name, len(hits), time.monotonic() - started, query, lang, region)
    if not looks_relevant(query, hits):
        note = f"decoy: {len(hits)} results, none mentions the query; engine is not trusting this client"
        log.warning("engine %s: %s", name, note)
        return failed(note)
    if not hits:
        # not cached on purpose: an empty page is as often a hiccup as a real
        # "nothing found", and remembering a hiccup for a day would mute the
        # engine for every later session
        return hits, EngineStatus(engine=name, ok=True, count=0, note="; ".join(n for n in ("no results", unused_note) if n))
    await cache.put(key, [h.model_dump() for h in hits])
    return hits, EngineStatus(engine=name, ok=True, count=len(hits), note=unused_note)
