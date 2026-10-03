"""public frontend instances: where they come from, which are resting, and
whether an answer is about the page asked for. which sites use which
frontend, and the paths they map to, are in sites.py.

instances come and go (anonymousoverflow: 1 of 34 answered on 2026-09-29), so
none is hard-coded. the list is libredirect's data.json, which its maintainers
refresh daily; it is cached for a day. instances named in FRONTENDS_PREFERRED
come first. an instance that fails is parked for FRONTEND_REST_S (a breaker
named "frontend:<host>", shared by every process), so later reads skip it.
"""

import logging
import re
from urllib.parse import urlsplit

log = logging.getLogger("weltsuche.frontends")

LIST_URL = "https://raw.githubusercontent.com/libredirect/instances/main/data.json"
LIST_CACHE_KEY = "frontends|libredirect"

# how long a failed instance is skipped. most failures seen were dead hosts
# (dns, tls, 5xx), which do not come back within the hour.
FRONTEND_REST_S = 6 * 3600

# instances tried per frontend per read; other routes of the site come after
MAX_TRIES = 3

# path words every url of a kind shares; they say nothing about the content
_STOP = {"comments", "status", "statuses", "watch", "shorts", "video", "videos", "questions", "wiki", "user", "users", "posts", "reel", "reels"}


def expected_tokens(url):
    """the words of the url's path that a page about it shows: path pieces
    (split at / - _ .) made of four or more letters in one case. ids are left
    out: a page shows its title, not its id, and an id is never a clean word
    (1umaxso, 2041171245027541434, instagram's DW90CiWiSim or DaVfopGlKhE).
    a url with nothing but ids gives no tokens and is not judged by them
    (found 2026-09-29: x.com/i/status/<id> parked every nitter instance, an
    instagram shortcode parked a working kittygram)."""
    pieces = re.split(r"[/_.\-]+", urlsplit(url).path)
    return {p.lower() for p in pieces
            if len(p) >= 4 and p.isalpha() and (p.islower() or p.isupper()) and p.lower() not in _STOP}


def relevant(page, tokens):
    """false when the page mentions none of the url's words: an instance that
    answers every path with its status or home page."""
    if not tokens:
        return True
    blob = f"{page.title} {page.text}".lower()
    return any(t in blob for t in tokens)


def park_reason(page):
    """why the instance that gave this failed answer should rest, or None
    when the answer speaks about the post, not the instance (a 404 is what
    every instance says about a deleted post; parking them all for it would
    wall the site off)."""
    if page.status in (400, 404, 410):
        return None
    return page.error


def _bucket(instance):
    return f"frontend:{urlsplit(instance).hostname}"


async def park(rt, instance, reason):
    await rt.breakers.open(_bucket(instance), FRONTEND_REST_S, reason)


async def _instance_list(rt):
    """libredirect's data.json and a note; ({}, reason) when it cannot be had."""
    data = await rt.cache.get(LIST_CACHE_KEY, rt.config.CACHE_TTL_S)
    if isinstance(data, dict):
        return data, ""
    host = urlsplit(LIST_URL).hostname
    try:
        resp = await rt.http.request("fetch", "GET", LIST_URL, lang="en", document=False, check=False, bucket=f"fetch:{host}")
        data = resp.json() if resp.status_code == 200 else None
    except ValueError as e:
        return {}, f"the libredirect instance list is not json ({e}); only FRONTENDS_PREFERRED was tried"
    if not isinstance(data, dict):
        return {}, f"the libredirect instance list answered http {resp.status_code}; only FRONTENDS_PREFERRED was tried"
    await rt.cache.put(LIST_CACHE_KEY, data)
    return data, ""


async def instances(rt, key):
    """(instance urls to try, preferred first and resting ones left out;
    a note when the list is incomplete or nothing is left to try).
    network errors loading the list propagate to the caller."""
    data, note = await _instance_list(rt)
    entry = data.get(key)
    if data and not isinstance(entry, dict):
        note = f"libredirect lists no {key!r} frontend"
    listed = entry.get("clearnet", []) if isinstance(entry, dict) else []
    urls = [u.rstrip("/") for u in dict.fromkeys([*rt.config.FRONTENDS_PREFERRED.get(key, []), *listed])]
    resting = {name for name, b in (await rt.breakers.all()).items() if b.open}
    ready = [u for u in urls if _bucket(u) not in resting]
    if not ready:
        why = f"all {len(urls)} {key} instances are resting after failures" if urls else f"no {key} instance is known"
        note = "; ".join(n for n in (note, why) if n)
    return ready, note
