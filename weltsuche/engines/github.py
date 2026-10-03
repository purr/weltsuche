"""github repositories through github's search api. the query may carry
github's own qualifiers: topic:mcp (the tags people give repos),
language:rust, stars:>100, in:readme, user:name, pushed:>2026-01-01.

signed in with the github cli's login when there is one (GITHUB_AUTH = "gh"):
`gh auth token` is read once per process, kept in memory only and sent to
api.github.com only. signed in, search allows 30 requests a minute and
github_code works; anonymous, 10 a minute and no code search (measured
2026-09-29). a developer who uses the github cli is already signed in;
this makes the same login serve weltsuche.
"""

import asyncio
import logging
import shutil
import time

from .. import net
from .common import NeedsLogin, QueryError, decode_json

log = logging.getLogger("weltsuche.engines.github")

NAME = "github"
HOME = "https://github.com/"
API = "https://api.github.com"

# seconds between two searches, (signed in, anonymous), from github's search
# limits: 30 a minute signed in, 10 anonymous, code search 10 and signed in
# only (docs.github.com, rest/search). each engine spaces by its own limit,
# not ENGINE_GAP_S: a search_many in three languages asks github within 4 s
# instead of about 12.
GAP_S = {"github": (2.0, 6.0), "github_code": (6.0, 6.0)}

# a missing login is looked for again after this long, so `gh auth login`
# takes effect without restarting the server
TOKEN_RECHECK_S = 300

_token = {}


# how long `gh auth token` may take before weltsuche searches anonymously
GH_TIMEOUT_S = 20


async def read_gh_token():
    """the github cli's token, "" without the cli or a login. an asyncio
    subprocess: the other tool calls go on while gh answers."""
    gh = shutil.which("gh")
    if gh is None:
        log.info("github: no gh cli on PATH; searching anonymously")
        return ""
    try:
        proc = await asyncio.create_subprocess_exec(gh, "auth", "token", "--hostname", "github.com",
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except OSError as e:
        log.warning("github: gh could not be started (%s); searching anonymously", e)
        return ""
    try:
        out, err = await asyncio.wait_for(proc.communicate(), GH_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        log.warning("github: gh auth token took more than %ds; searching anonymously", GH_TIMEOUT_S)
        return ""
    if proc.returncode != 0:
        log.info("github: gh is not logged in (%s); searching anonymously", " ".join(err.decode(errors="replace").split())[:120])
        return ""
    return out.decode(errors="replace").strip()


async def token(config):
    """the github token to send, "" for anonymous. read once per process,
    and again every TOKEN_RECHECK_S while there is none."""
    stale = not _token.get("value") and time.monotonic() - _token.get("at", 0.0) > TOKEN_RECHECK_S
    if "value" not in _token or stale:
        _token["value"] = await read_gh_token() if config.GITHUB_AUTH == "gh" else ""
        _token["at"] = time.monotonic()
    return _token["value"]


def _raise_for(resp, signed_in):
    """github's refusals: a rate limit (403 or 429 with no requests left or
    a retry-after) rests the engine until github's reset, not for an hour."""
    status = resp.status_code
    if status in (403, 429):
        retry = resp.headers.get("retry-after") or ""
        reset = resp.headers.get("x-ratelimit-reset") or ""
        if retry.isdigit() or resp.headers.get("x-ratelimit-remaining") == "0":
            wait = int(retry) if retry.isdigit() else max(1, int(reset) - int(time.time())) if reset.isdigit() else None
            raise net.RateLimited(f"github search limit reached ({'signed in' if signed_in else 'anonymous'})", wait)
        if status == 403:
            # a secondary rate limit, which github asks to wait out for a
            # minute at least; parked for an hour as a block, it was not
            raise net.RateLimited("github refused (http 403): a secondary rate limit", 60)
    if status == 401:
        # forget the token so the next call, after the breaker's rest, reads
        # gh's login again instead of resending the rejected one
        _token.clear()
        raise net.Blocked("github rejected the gh login (http 401); run gh auth refresh")
    if status == 422:
        message = ""
        try:
            message = resp.json().get("message", "")
        except ValueError:
            pass  # no json body; the status says enough
        raise QueryError(f"github: {message or 'unprocessable query'}")
    net.classify(resp, markers=False)


async def headers(config, accept="application/vnd.github+json"):
    """the headers of a request to api.github.com, and to nowhere else:
    signed in when a login is available."""
    auth = await token(config)
    out = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    if auth:
        out["Authorization"] = f"Bearer {auth}"
    return out


async def api(http, engine, path, params, *, accept="application/vnd.github+json", require_login=False):
    """one github api GET for `engine` (a key of GAP_S, which is also its
    spacing bucket), signed in when possible; the decoded json."""
    sent = await headers(http.config, accept)
    signed_in = "Authorization" in sent
    if require_login and not signed_in:
        raise NeedsLogin(f"needs a github login: install the github cli and run gh auth login; weltsuche "
                         f"looks for it again within {TOKEN_RECHECK_S // 60} minutes")
    resp = await http.request(NAME, "GET", API + path, lang="en", document=False, check=False, params=params,
                              extra_headers=sent, bucket=engine, gap=GAP_S[engine][0 if signed_in else 1])
    _raise_for(resp, signed_in)
    return decode_json(resp)


async def search(http, query, lang, region, n):
    data = await api(http, NAME, "/search/repositories", {"q": query, "per_page": n})
    hits = []
    for repo in data.get("items") or []:
        facts = [repo.get("description") or "", f"★{repo.get('stargazers_count', 0):,}", repo.get("language") or ""]
        if repo.get("topics"):
            facts.append("topics: " + ", ".join(repo["topics"][:8]))
        if repo.get("pushed_at"):
            facts.append("pushed " + repo["pushed_at"][:10])
        hits.append({"title": repo.get("full_name", ""), "url": repo.get("html_url", ""),
                     "snippet": " · ".join(f for f in facts if f)})
    return hits
