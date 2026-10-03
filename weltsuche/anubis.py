"""anubis, the sha-256 proof-of-work gate in front of startpage and a growing
number of sites (winehq's bugzilla, many software forges).

a browser pays it in javascript: it reads a challenge, finds a nonce whose
sha256(challenge + nonce) starts with `difficulty` hex zeros, and calls
pass-challenge, which hands out an auth cookie. this module pays it the same
way, once per page load that asks for it. it asks for compute, not for a
human, so paying it is what every visitor does; anything beyond a proof of
work (a difficulty past MAX_DIFFICULTY, an algorithm other than fast/slow) is
refused as a block. the protocol is the one searxng's startpage engine
implements.

two generations are live, both read from their own client script 2026-09-29:
- v1.26 (startpage): the challenge rides in the page as
  <script id="anubis_challenge">{"challenge": {"id", "randomData", ...},
  "rules"}</script> and pass-challenge wants the `id` back. the page also
  sets a `*-cookie-verification` cookie that pass-challenge checks, which is
  why clock skew broke it (see net.SKEW_TOLERANCE_S).
- v1.15 (bugs.winehq.org): the page only loads the script, which POSTs
  api/make-challenge for {"challenge": "<hex>", "rules"}; pass-challenge
  takes no id.

success is not judged by a cookie name (startpage's has changed before): the
caller loads the page again and checks `detect` on what comes back.
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from urllib.parse import urlsplit

from . import net

log = logging.getLogger("weltsuche.anubis")

API = "/.within.website/x/cmd/anubis/api/"

#: startpage has served 4 and 5. 6 averages 17 million hashes, some 50 seconds
#: at the 0.35 million a second python managed here, bearable now and then; 7
#: would be many minutes and is not meant to be paid by a client like this one.
MAX_DIFFICULTY = 6

# the challenge json (v1.2x) or the anubis script tag (every version). an
# article that merely talks about anubis shows these escaped, never as tags.
_PAGE = re.compile(r'<script[^>]+(?:id="anubis_challenge"|src="[^"]*/\.within\.website/x/cmd/anubis/)', re.I)


def detect(html):
    """true when `html` is an anubis challenge page."""
    return bool(html) and _PAGE.search(html[:50000]) is not None


def _embedded(html, element_id):
    m = re.search(rf'<script id="{element_id}" type="application/json">(.*?)</script>', html, re.S)
    return json.loads(m.group(1)) if m else None


def solve(data, difficulty):
    """the anubis "fast" rule: sha256(data + str(nonce)) with `difficulty`
    leading hex zeros. cpu-bound, run it in a thread."""
    prefix = "0" * difficulty
    blob = data.encode()
    for nonce in range(16 ** difficulty * 8):  # 8x the expected search; the miss chance is e^-8
        digest = hashlib.sha256(blob + str(nonce).encode()).hexdigest()
        if digest.startswith(prefix):
            return nonce, digest
    raise net.Blocked("anubis: no nonce found within the search budget")


# the base prefix is a path on the page's own origin. anything else in it
# ("@192.168.1.1/x", "//evil") would turn the toll request into a request to
# another host (found in review 2026-09-29), so it is refused.
_PREFIX = re.compile(r"(/[A-Za-z0-9._~/-]*)?")


def _challenge(payload, origin):
    """(data, challenge id or None, algorithm, difficulty) from a challenge
    payload; net.Blocked for anything that is not a proof of work."""
    if not isinstance(payload, dict):
        raise net.Blocked(f"anubis at {origin}: the challenge is a json {type(payload).__name__}, not an object")
    challenge, rules = payload.get("challenge"), payload.get("rules")
    rules = rules if isinstance(rules, dict) else {}
    data, cid = (challenge.get("randomData"), challenge.get("id")) if isinstance(challenge, dict) else (challenge, None)
    algorithm = rules.get("algorithm", "")
    try:
        difficulty = int(rules.get("difficulty", 0))
    except (TypeError, ValueError) as e:
        raise net.Blocked(f"anubis at {origin}: difficulty {rules.get('difficulty')!r} is not a number") from e
    if not isinstance(data, str) or not data or algorithm not in ("fast", "slow") or not 0 <= difficulty <= MAX_DIFFICULTY:
        raise net.Blocked(f"anubis at {origin}: {algorithm!r} difficulty {difficulty} is more than this client pays")
    return data, cid, algorithm, difficulty


async def pay(http, name, page_url, html, lang):
    """pay the toll the challenge page `html` (served at `page_url`) asks for.
    raises net.Blocked when anubis wants more than a proof of work or refuses
    the payment, net.UnsafeUrl when the toll would leave the public web."""
    parts = urlsplit(page_url)
    origin = f"{parts.scheme}://{parts.netloc}"
    try:
        prefix = _embedded(html, "anubis_base_prefix") or ""
        embedded = _embedded(html, "anubis_challenge")
    except ValueError as e:
        raise net.Blocked(f"anubis at {origin}: the challenge page carries broken json ({e})") from e
    if not isinstance(prefix, str) or not _PREFIX.fullmatch(prefix):
        raise net.Blocked(f"anubis at {origin}: base prefix {prefix!r} is not a path; not following it")
    api = origin + prefix.rstrip("/") + API
    await net.vet_url(api)
    if embedded is None:
        resp = await http.request(name, "POST", api + "make-challenge", lang=lang, referer=page_url,
                                  document=False, check=False, allow_redirects=False, spaced=False)
        try:
            embedded = resp.json()
        except ValueError as e:
            raise net.Blocked(f"anubis at {origin}: make-challenge answered http {resp.status_code} without json") from e
    data, cid, algorithm, difficulty = _challenge(embedded, origin)

    started = time.monotonic()
    nonce, digest = await asyncio.to_thread(solve, data, difficulty)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    params = {"response": digest, "nonce": nonce, "redir": page_url, "elapsedTime": elapsed_ms}
    if cid:
        params = {"id": cid, **params}
    resp = await http.request(name, "GET", api + "pass-challenge", lang=lang, referer=page_url, params=params,
                              check=False, allow_redirects=False, spaced=False)
    log.info("anubis: %s toll, %s difficulty %d, %d hashes in %dms -> http %d", origin, algorithm, difficulty, nonce + 1, elapsed_ms, resp.status_code)
    if resp.status_code >= 400:
        raise net.Blocked(f"anubis at {origin} refused the toll: http {resp.status_code}")
