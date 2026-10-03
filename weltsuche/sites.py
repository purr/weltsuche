"""sites that wall off plain clients, and every route that still reads them.

a plain client reads nothing from reddit (javascript bot check), x, instagram,
threads and bluesky (javascript apps), youtube (a page whose only text is its
footer), tiktok (captcha) or stack exchange (403). each site below lists its
routes in order of preference: the site's own public api or embed where one
exists, then public frontends (frontends.py), then archives. `fetch` runs them
through `_race`: the next route starts when one fails or is slow, the first
good answer wins, and every failure is named in the reply.

measured 2026-09-29 (telegram, naver blog, crates.io, pubmed: 2026-10-03):
  reddit     arctic shift and pullpush archives: post and full comment tree;
             redlib: 2 of 7 instances; reddit's own rss and json: 403
  x          fxtwitter api (the fixupx.com / fxtwitter.com project): tweet
             text, counts, community note; nitter: 1 of 7; vxtwitter: 403
  youtube    youtube's player api (android client): title, channel, views,
             description, caption tracks and their text; invidious: 2 of 7
  instagram  kittygram: 3 of 8 (post captions); instagram's embed page
  tiktok     tiktok's oembed: the caption of a video; proxitok: 0 of 14
  threads    threads' embed page: the post and its counts
  bluesky    bluesky's public api: the post and its replies
  telegram   a channel post through its embed page (t.me/channel/123?embed=1)
  medium     reads directly; libmedium when it does not
  stack ex.  the stack exchange api (300 requests a day without a key)
  code file  a file page on github, codeberg or gitlab is read as the raw
             file (github's shows line numbers only, gitlab's is javascript)
  github     a repository's front page: its facts (stars, license, push
             date) and readme through github's api, else the page itself
  gitee      a bot wall (http 405) on every page; its public api reads a
             repository (facts and readme) and a file
  naver blog the post sits in a frame the page builds by javascript; the
             mobile page (m.blog.naver.com) carries the post itself
  crates.io  a javascript app; its api reads a crate's facts and readme
  pubmed     a cookie check (http 203) for plain clients; ncbi's e-utilities
             give the citation and abstract as text
  amazon     a product page shows a session it does not know an interstitial;
             read through the amazon engine's session (engines/amazon.py)
  airbnb     a room page is a javascript app; its data (facts, ratings) and
             its json-ld (description) are read (engines/airbnb.py)
no route found: quora (quetre 0 of 22), imdb (libremdb 0 of 23), bilibili's
api (412), zhihu's api (403). a site not listed here is read directly.
"""

import base64
import html as htmllib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import parse_qs, quote, urlsplit
from xml.etree import ElementTree

from curl_cffi.requests.exceptions import RequestException

from . import net
from .engines import airbnb, amazon, crates, github
from .engines.common import ParseError
from .models import Page
from .text import visible_text

# youtube's player api answers per client; the android client is the one
# whose caption urls work without a proof-of-origin token (the web client's
# came back empty, 2026-09-29). the version is the one that answered then.
YOUTUBE_CLIENT = {"clientName": "ANDROID", "clientVersion": "20.10.38"}

# a transcript is cut into paragraphs of about this many seconds, each opened
# with its timestamp, so a quote can be found in the video
TRANSCRIPT_PARAGRAPH_S = 30

REDDIT_COMMENT_DEPTH = 8
BLUESKY_REPLY_DEPTH = 6


# -- answers ------------------------------------------------------------------

def api_page(url, via, text, notes, **meta):
    """a page read through an api: `url` is what the model cites, `via` the
    api asked, `notes` which api it was and what the answer leaves out."""
    return Page(url=url, via=via, status=200, content_type="application/json", kind="api",
                text=text, notes=[n for n in notes if n], **meta)


class ApiError(Exception):
    """an api answered, but not with the data asked for. the route runner in
    fetch.py reports it under the route's name."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _iso(ts):
    return datetime.fromtimestamp(float(ts), timezone.utc).date().isoformat() if ts else ""


def _api_error(resp):
    """ApiError for an api answer other than 200: the status, what the api
    said when it says it in json (stack exchange's throttle message, github's
    reason), and whether a retry can help."""
    status = resp.status_code
    try:
        body = resp.json()
    except ValueError:
        body = None  # an html or empty error page: the status says enough
    said = (body.get("error_message") or body.get("message") or "") if isinstance(body, dict) else ""
    text = f"http {status}" + (f": {' '.join(str(said).split())[:200]}" if said else "")
    if status == 429 or status >= 500:
        wait = net.retry_after_s(resp.headers.get("retry-after"))
        text += f"; retry in {wait}s" if wait is not None else "; worth a retry later"
    return ApiError(text, status)


async def _json(rt, url, params=None, *, method="GET", json_body=None, headers=None, spaced=True):
    """one api request through the fetch session, spaced per api host. the
    decoded json object; ApiError for any other answer, RequestException
    for a network failure. `spaced=False` for the second call of one page
    read (the answers after the question), which would otherwise wait the
    full gap behind the first."""
    resp = await rt.http.request("fetch", method, url, lang="en", document=False, check=False, params=params,
                                 json_body=json_body, extra_headers=headers, spaced=spaced,
                                 bucket=f"fetch:{urlsplit(url).hostname}")
    if resp.status_code != 200:
        raise _api_error(resp)
    try:
        data = resp.json()
    except ValueError as e:
        raise ApiError(f"http 200 but not json ({resp.headers.get('content-type', 'no content type')})") from e
    if not isinstance(data, dict):
        raise ApiError(f"answered a json {type(data).__name__}, not an object")
    return data


async def _text(rt, url, params=None, *, headers=None, spaced=True):
    """one api request that answers with text rather than json, through the
    fetch session, spaced per api host; ApiError for anything but http 200
    with a body."""
    resp = await rt.http.request("fetch", "GET", url, lang="en", document=False, check=False, params=params,
                                 extra_headers=headers, spaced=spaced, bucket=f"fetch:{urlsplit(url).hostname}")
    if resp.status_code != 200:
        raise _api_error(resp)
    if not resp.text.strip():
        raise ApiError("http 200 with an empty body", 200)
    return resp.text


# -- routes -------------------------------------------------------------------

def _always(parts):
    return True


@dataclass(frozen=True)
class Api:
    """the site's own api, or a public service that wraps it."""
    label: str
    read: Callable            # async (rt, url, lang) -> models.Page; raises ApiError
    applies: Callable = _always


@dataclass(frozen=True)
class Frontend:
    """a libredirect frontend; `path` maps the url onto an instance."""
    key: str
    path: Callable
    applies: Callable = _always


@dataclass(frozen=True)
class Load:
    """a page loaded like any other: the site itself (`url` the identity) or
    its embed page. `thin_is_weak`: a thin answer is kept only as a last
    resort, the next route is tried. `short`: the page is short by design
    (an embed), which says nothing about javascript."""
    label: str
    url: Callable
    applies: Callable = _always
    thin_is_weak: bool = False
    short: bool = False


@dataclass(frozen=True)
class Site:
    name: str
    hosts: tuple
    routes: tuple
    # whether the page text mentions the url's path words (see
    # frontends.relevant). a youtube url's only word is the video id.
    words_in_text: bool = True
    match: Callable | None = field(default=None, compare=False)


# -- reddit -------------------------------------------------------------------

_REDDIT_POST = re.compile(r"/comments/([a-z0-9]+)", re.I)


def _reddit_post(parts):
    m = _REDDIT_POST.search(parts.path)
    return m.group(1).lower() if m else None


def _reddit_text(post, comments):
    """post then comments, one line per comment, indented by depth."""
    lines = [
        f"r/{post.get('subreddit', '')} · u/{post.get('author', '')} · score {post.get('score', '?')} · "
        f"{post.get('num_comments', '?')} comments · {_iso(post.get('created_utc'))}",
        htmllib.unescape(post.get("title", "")),
        htmllib.unescape(post.get("selftext") or post.get("url") or ""),
        "",
        "comments:" if comments else "no comments archived",
    ]
    for depth, c in comments:
        body = " ".join(htmllib.unescape(c.get("body", "")).split())
        lines.append(f"{'  ' * depth}- u/{c.get('author', '')} ({c.get('score', '?')}): {body}")
    return "\n".join(lines)


def _walk_arctic(children, depth, out):
    for child in children or []:
        if child.get("kind") != "t1" or depth > REDDIT_COMMENT_DEPTH:
            continue
        data = child.get("data", {})
        out.append((depth, data))
        replies = data.get("replies")
        if isinstance(replies, dict):
            _walk_arctic(replies.get("data", {}).get("children"), depth + 1, out)


async def reddit_arctic(rt, url, lang):
    post_id = _reddit_post(urlsplit(url))
    base = "https://arctic-shift.photon-reddit.com/api"
    posts = (await _json(rt, f"{base}/posts/ids", {"ids": post_id})).get("data") or []
    if not posts:
        raise ApiError(f"post {post_id} is not in its archive (yet)", 404)
    tree = await _json(rt, f"{base}/comments/tree", {"link_id": post_id, "limit": 1000})
    comments = []
    _walk_arctic(tree.get("data"), 0, comments)
    post = posts[0]
    missing = post.get("num_comments") and len(comments) < post["num_comments"]
    return api_page(url, f"{base}/posts/ids?ids={post_id}", _reddit_text(post, comments),
                    ["read through the arctic shift reddit archive",
                     f"{len(comments)} of {post['num_comments']} comments archived" if missing else ""],
                    title=htmllib.unescape(post.get("title", "")), author=post.get("author", ""),
                    date=_iso(post.get("created_utc")), sitename="reddit")


async def reddit_pullpush(rt, url, lang):
    post_id = _reddit_post(urlsplit(url))
    base = "https://api.pullpush.io/reddit/search"
    posts = (await _json(rt, f"{base}/submission/", {"ids": post_id})).get("data") or []
    if not posts:
        raise ApiError(f"post {post_id} is not in its archive", 404)
    flat = await _json(rt, f"{base}/comment/", {"link_id": post_id, "size": 100})
    children = {}
    for c in flat.get("data") or []:
        children.setdefault(c.get("parent_id", ""), []).append(c)
    comments = []

    def walk(parent, depth):
        for c in sorted(children.get(parent, []), key=lambda c: -(c.get("score") or 0)):
            if depth <= REDDIT_COMMENT_DEPTH:
                comments.append((depth, c))
                walk(f"t1_{c.get('id')}", depth + 1)

    walk(f"t3_{post_id}", 0)
    post = posts[0]
    return api_page(url, f"{base}/submission/?ids={post_id}", _reddit_text(post, comments),
                    ["read through the pullpush reddit archive (at most 100 comments)"],
                    title=htmllib.unescape(post.get("title", "")), author=post.get("author", ""),
                    date=_iso(post.get("created_utc")), sitename="reddit")


# -- x ------------------------------------------------------------------------

_X_STATUS = re.compile(r"^/([A-Za-z0-9_]{1,15})/status/(\d+)")
_X_PROFILE = re.compile(r"^/([A-Za-z0-9_]{1,15})/?$")
_X_RESERVED = {"home", "explore", "search", "i", "settings", "messages", "notifications", "login", "signup", "tos", "privacy"}


def _x_status(parts):
    m = _X_STATUS.match(parts.path)
    return m.groups() if m else None


def _x_profile(parts):
    m = _X_PROFILE.match(parts.path)
    return m.group(1) if m and m.group(1).lower() not in _X_RESERVED else None


def _tweet_text(t):
    author = t.get("author") or {}
    lines = [
        f"{author.get('name', '')} (@{author.get('screen_name', '')}) · {t.get('created_at', '')}",
        t.get("text", ""),
    ]
    for m in ((t.get("media") or {}).get("all") or []):
        if m.get("altText"):
            lines.append(f"[{m.get('type', 'media')}: {m['altText']}]")
    if t.get("quote"):
        q = t["quote"]
        lines.append(f"quoting @{(q.get('author') or {}).get('screen_name', '')}: {q.get('text', '')}")
    if t.get("community_note"):
        lines.append(f"community note: {(t['community_note'] or {}).get('text', '')}")
    lines.append(f"replies {t.get('replies', '?')} · reposts {t.get('retweets', '?')} · likes {t.get('likes', '?')} · views {t.get('views', '?')}")
    if t.get("replying_to"):
        lines.insert(1, f"replying to @{t['replying_to']}")
    return "\n".join(lines)


async def x_fxtwitter_status(rt, url, lang):
    user, status = _x_status(urlsplit(url)) or ("", "")
    # the screen name is optional to fxtwitter; x.com/i/status/<id> has none
    api = f"https://api.fxtwitter.com/{user}/status/{status}" if user.lower() != "i" else f"https://api.fxtwitter.com/status/{status}"
    data = await _json(rt, api)
    tweet = data.get("tweet")
    if not tweet:
        raise ApiError(data.get("message") or "no tweet in the answer", data.get("code"))
    author = tweet.get("author") or {}
    return api_page(url, api, _tweet_text(tweet),
                    ["read through the fxtwitter api (fixupx.com); the post only, replies are not included"],
                    title=f"{author.get('name', '')} on x", author=author.get("screen_name", ""),
                    date=_iso(tweet.get("created_timestamp")), sitename="x")


async def x_fxtwitter_profile(rt, url, lang):
    user = _x_profile(urlsplit(url))
    api = f"https://api.fxtwitter.com/{user}"
    data = await _json(rt, api)
    u = data.get("user")
    if not u:
        raise ApiError(data.get("message") or "no user in the answer", data.get("code"))
    text = "\n".join([
        f"{u.get('name', '')} (@{u.get('screen_name', '')})", u.get("description", ""),
        f"followers {u.get('followers', '?')} · following {u.get('following', '?')} · posts {u.get('tweets', '?')} · joined {u.get('joined', '?')}",
    ])
    return api_page(url, api, text, ["read through the fxtwitter api: the profile only, no posts (nitter, which lists them, did not answer)"],
                    title=f"{u.get('name', '')} on x", author=u.get("screen_name", ""), sitename="x")


# -- youtube ------------------------------------------------------------------

def youtube_video(parts):
    host = parts.hostname or ""
    if host.endswith("youtu.be"):
        video = parts.path.strip("/").split("/")[0]
    elif parts.path.startswith(("/shorts/", "/live/", "/embed/")):
        video = parts.path.split("/")[2]
    else:
        video = parse_qs(parts.query).get("v", [""])[0]
    return video if re.fullmatch(r"[A-Za-z0-9_-]{11}", video or "") else None


def _invidious_path(parts):
    video = youtube_video(parts)
    return f"/watch?v={video}" if video else parts.path + (f"?{parts.query}" if parts.query else "")


def _pick_track(tracks, lang):
    """a manual track in `lang`, else the auto-generated one in `lang`, else
    the auto-generated one (the spoken language), else the first."""
    def find(pred):
        return next((t for t in tracks if pred(t)), None)

    def in_lang(t):
        return (t.get("languageCode") or "").split("-")[0] == lang

    return (find(lambda t: in_lang(t) and t.get("kind") != "asr") or find(in_lang)
            or find(lambda t: t.get("kind") == "asr") or tracks[0])


def _transcript(xml):
    """timed text xml to paragraphs of about TRANSCRIPT_PARAGRAPH_S seconds.
    ValueError when the answer is not timed text."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as e:
        raise ValueError(f"not timed text: {e}") from e
    paragraphs, current, opened = [], [], None
    for node in root.iter("text"):
        start = float(node.get("start") or 0)
        if opened is None:
            opened = start
        elif start - opened >= TRANSCRIPT_PARAGRAPH_S:
            paragraphs.append((opened, current))
            current, opened = [], start
        # the xml parser removed one layer of escaping; the text was escaped
        # before it went into the xml
        current.append(" ".join(htmllib.unescape(node.text or "").split()))
    if current:
        paragraphs.append((opened, current))
    return "\n".join(f"[{int(t) // 60}:{int(t) % 60:02d}] {' '.join(words)}" for t, words in paragraphs)


async def youtube_player(rt, url, lang):
    video = youtube_video(urlsplit(url))
    api = "https://www.youtube.com/youtubei/v1/player"
    data = await _json(rt, api, method="POST", json_body={"context": {"client": YOUTUBE_CLIENT}, "videoId": video})
    status = data.get("playabilityStatus") or {}
    details = data.get("videoDetails") or {}
    if status.get("status") != "OK" and not details:
        raise ApiError(f"{status.get('status', 'no playability status')}: {status.get('reason', '')}".rstrip(": "))
    length = int(details.get("lengthSeconds") or 0)
    head = [
        details.get("title", ""),
        f"{details.get('author', '')} · {int(details.get('viewCount') or 0):,} views · {length // 60}:{length % 60:02d}",
        "",
        details.get("shortDescription", ""),
    ]
    notes, partial = [], False
    tracks = (data.get("captions") or {}).get("playerCaptionsTracklistRenderer", {}).get("captionTracks") or []
    if tracks:
        track = _pick_track(tracks, lang)
        name = f"{track.get('languageCode')}{' auto-generated' if track.get('kind') == 'asr' else ''}"
        try:
            resp = await rt.http.request("fetch", "GET", track["baseUrl"].replace("&fmt=srv3", ""), lang=lang, document=False,
                                         check=False, spaced=False, bucket="fetch:www.youtube.com")
            body = resp.text if resp.status_code == 200 else ""
            reason = "" if body else f"http {resp.status_code}, {len(resp.content or b'')} bytes"
        except RequestException as e:
            body, reason = "", net.describe(e)
        try:
            transcript = _transcript(body) if body else ""
        except ValueError as e:
            transcript, reason = "", str(e)
        if transcript:
            head += ["", f"transcript ({name}):", transcript]
        else:
            notes.append(f"the {name} transcript could not be read ({reason or 'no text in it'})")
            # a refused or failed request may pass; an empty track will not
            partial = bool(reason)
        others = sorted({f"{t.get('languageCode')}{'-auto' if t.get('kind') == 'asr' else ''}" for t in tracks} - {name.replace(' auto-generated', '-auto')})
        if others:
            notes.append(f"other transcripts: {', '.join(others)} (fetch again with that lang)")
    else:
        notes.append("the video has no transcript")
    return api_page(url, f"{api} (android client)", "\n".join(head).strip(), ["read through youtube's player api", *notes],
                    partial=partial,
                    title=details.get("title", ""), author=details.get("author", ""), sitename="youtube")


# -- instagram, tiktok, threads, bluesky ---------------------------------------

_INSTAGRAM_POST = re.compile(r"^/(?:[A-Za-z0-9_.]+/)?(p|reel|tv)/([A-Za-z0-9_-]+)")


def _instagram_embed(parts):
    m = _INSTAGRAM_POST.match(parts.path)
    if m is None:
        raise ApiError("not an instagram post url")
    return f"https://www.instagram.com/{m.group(1)}/{m.group(2)}/embed/captioned/"


def _tiktok_video(parts):
    return re.match(r"^/@[^/]+/video/(\d+)", parts.path) is not None


async def tiktok_oembed(rt, url, lang):
    api = "https://www.tiktok.com/oembed"
    data = await _json(rt, api, {"url": url})
    if not data.get("title") and not data.get("author_name"):
        raise ApiError("no caption or author in the answer")
    text = f"{data.get('author_name', '')} ({data.get('author_url', '')})\n{data.get('title', '')}"
    return api_page(url, f"{api}?url={quote(url, safe='')}", text,
                    ["read through tiktok's oembed: the caption only (no comments, no transcript)"],
                    title=f"{data.get('author_name', '')} on tiktok", author=data.get("author_name", ""), sitename="tiktok")


_THREADS_POST = re.compile(r"^/@[^/]+/post/[A-Za-z0-9_-]+")
_TELEGRAM_POST = re.compile(r"^/(?:s/)?([A-Za-z0-9_]{4,32})/(\d+)/?$")


def _telegram_embed(parts):
    channel, post = _TELEGRAM_POST.match(parts.path).groups()
    return f"https://t.me/{channel}/{post}?embed=1"
_BLUESKY_POST = re.compile(r"^/profile/([^/]+)/post/([a-z0-9]+)")


def _bluesky_lines(node, depth, out):
    post = node.get("post") or {}
    record = post.get("record") or {}
    author = (post.get("author") or {}).get("handle", "")
    counts = f"{post.get('likeCount', 0)} likes, {post.get('repostCount', 0)} reposts, {post.get('replyCount', 0)} replies"
    text = " ".join((record.get("text") or "").split())
    out.append(f"{'  ' * depth}- @{author} · {(record.get('createdAt') or '')[:10]} · {counts}: {text}" if depth
               else f"@{author} · {(record.get('createdAt') or '')[:10]} · {counts}\n{record.get('text', '')}\n\nreplies:")
    if depth < BLUESKY_REPLY_DEPTH:
        for reply in node.get("replies") or []:
            _bluesky_lines(reply, depth + 1, out)


async def bluesky_thread(rt, url, lang):
    m = _BLUESKY_POST.match(urlsplit(url).path)
    if m is None:
        raise ApiError("not a bluesky post url")
    actor, rkey = m.groups()
    api = "https://public.api.bsky.app/xrpc/app.bsky.feed.getPostThread"
    data = await _json(rt, api, {"uri": f"at://{actor}/app.bsky.feed.post/{rkey}", "depth": BLUESKY_REPLY_DEPTH})
    thread = data.get("thread") or {}
    if "post" not in thread:
        raise ApiError(thread.get("$type") or "no post in the answer", 404)
    lines = []
    _bluesky_lines(thread, 0, lines)
    author = (thread["post"].get("author") or {}).get("handle", "")
    return api_page(url, api, "\n".join(lines), ["read through bluesky's public api, with replies"],
                    title=f"@{author} on bluesky", author=author,
                    date=((thread["post"].get("record") or {}).get("createdAt") or "")[:10], sitename="bluesky")


# -- stack exchange -----------------------------------------------------------

_SE_SITES = {
    "stackoverflow.com": "stackoverflow", "superuser.com": "superuser", "serverfault.com": "serverfault",
    "askubuntu.com": "askubuntu", "mathoverflow.net": "mathoverflow", "stackapps.com": "stackapps",
}
_SE_QUESTION = re.compile(r"^/(?:questions|q)/(\d+)")


def stackexchange_target(url):
    """(api site name, question id) for a stack exchange question url, else None.
    covers the localized stack overflows (ru., ja., pt., es.) and *.stackexchange.com."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    site = _SE_SITES.get(host)
    if site is None and host.count(".") == 2 and host.endswith((".stackoverflow.com", ".stackexchange.com")):
        site = host.removesuffix(".com") if host.endswith(".stackoverflow.com") else host.removesuffix(".stackexchange.com")
    m = _SE_QUESTION.match(parts.path)
    return (site, m.group(1)) if site and m else None


async def stackexchange(rt, url, lang):
    """a question and its answers, best first, through the official api. the
    sites answer plain clients with 403 (measured 2026-09-29); the api serves
    300 requests a day per address without a key."""
    site, question_id = stackexchange_target(url) or ("", "")
    api = f"https://api.stackexchange.com/2.3/questions/{question_id}"
    params = {"site": site, "filter": "withbody"}
    q = await _json(rt, api, params)
    a = await _json(rt, api + "/answers", {**params, "sort": "votes", "order": "desc", "pagesize": 30}, spaced=False)
    # the api asks for a pause with `backoff` seconds; the next request to it
    # waits that long (the breaker makes it answer "resting")
    backoff = max(int(q.get("backoff") or 0), int(a.get("backoff") or 0))
    if backoff:
        await rt.breakers.open(f"fetch:{urlsplit(api).hostname}", backoff, "the stack exchange api asked for a pause")
    if not q.get("items"):
        raise ApiError(f"no question {question_id} on {site}", 404)
    question = q["items"][0]
    parts = [f"question (score {question.get('score')}; tags: {', '.join(question.get('tags', []))})", visible_text(question.get("body", ""))]
    answers = a.get("items", [])
    for answer in answers:
        parts.append(f"answer (score {answer.get('score')}{'; accepted' if answer.get('is_accepted') else ''})")
        parts.append(visible_text(answer.get("body", "")))
    total = question.get("answer_count", len(answers))
    shown = f"{len(answers)} of {total} answers, best first" if total > len(answers) else f"{len(answers)} answers"
    return api_page(url, f"{api}?site={site}", "\n\n".join(parts),
                    [f"read through the stack exchange api, {shown} (api quota left today: {a.get('quota_remaining')})"],
                    title=htmllib.unescape(question.get("title", "")),
                    author=htmllib.unescape((question.get("owner") or {}).get("display_name", "")),
                    date=_iso(question.get("creation_date")), sitename=site)


# -- code hosts ---------------------------------------------------------------

def raw_file_url(url):
    """the raw file behind a file page on github, codeberg or gitlab, else
    None. the path after the repository is kept whole, so a branch name with
    a slash in it maps as it is."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    segs = parts.path.split("/")  # "", owner, repo, kind, ref, path...
    if host == "github.com" and len(segs) > 5 and segs[3] in ("blob", "raw"):
        return "https://raw.githubusercontent.com/" + "/".join(segs[1:3] + segs[4:])
    if host == "codeberg.org" and len(segs) > 6 and segs[3] == "src":
        return "https://codeberg.org/" + "/".join(segs[1:3] + ["raw"] + segs[4:])
    if host == "gitlab.com" and "/-/blob/" in parts.path:
        return "https://gitlab.com" + parts.path.replace("/-/blob/", "/-/raw/", 1)
    return None


def _gitee_parts(parts):
    """(owner, repo, ref, file path) of a gitee url; ref and path are "" for
    the repository's front page, None for any other page."""
    segs = parts.path.rstrip("/").split("/")
    if len(segs) == 3:
        return segs[1], segs[2], "", ""
    if len(segs) > 5 and segs[3] == "blob":
        return segs[1], segs[2], segs[4], "/".join(segs[5:])
    return None


def _base64_text(item):
    return base64.b64decode(item.get("content") or "").decode("utf-8", "replace")


async def _readme(rt, url, host, headers=None):
    """(readme text, note, partial) of a repository whose facts were just
    read; a repository without a readme (404) is still a whole answer, one
    whose readme request failed otherwise is partial, so it is not cached."""
    try:
        item = await _json(rt, url, headers=headers, spaced=False)
        return _base64_text(item), f"the repository's facts and readme, read through {host}'s api", False
    except ApiError as e:
        return "", f"the repository's facts through {host}'s api; no readme ({e})", e.status != 404
    except RequestException as e:
        return "", f"the repository's facts through {host}'s api; the readme could not be read ({net.describe(e)})", True


def _github_repo(parts):
    """(owner, repo) of a github repository's front page, else None. a
    reserved path of the same shape (/topics/mcp) fails at the api, and
    the page is then read as it is."""
    host = (parts.hostname or "").lower().removeprefix("www.")
    segs = parts.path.strip("/").split("/")
    if host == "github.com" and len(segs) == 2 and all(segs):
        return segs[0], segs[1].removesuffix(".git")
    return None


async def github_repo(rt, url, lang):
    """a github repository's facts and readme through github's api, signed in
    with the github cli's login when there is one (engines/github.py). the
    page itself shows the readme amid navigation, without license or push
    date, and a model reading raw api json paged through it in slices."""
    owner, repo = _github_repo(urlsplit(url)) or ("", "")
    api = f"https://api.github.com/repos/{owner}/{repo}"
    headers = await github.headers(rt.config)
    info = await _json(rt, api, headers=headers)
    license_id = (info.get("license") or {}).get("spdx_id")
    facts = [info.get("description") or "", f"★{info.get('stargazers_count', 0):,}", f"{info.get('forks_count', 0):,} forks",
             info.get("language") or "", f"license {license_id}" if license_id else "no license file",
             "topics: " + ", ".join(info["topics"]) if info.get("topics") else "",
             f"pushed {(info.get('pushed_at') or '')[:10]}", "archived" if info.get("archived") else "",
             f"{info.get('open_issues_count', 0)} open issues and pull requests"]
    readme, note, partial = await _readme(rt, api + "/readme", "github", headers)
    return api_page(url, api, " · ".join(f for f in facts if f) + ("\n\n" + readme if readme else ""), [note],
                    title=info.get("full_name") or f"{owner}/{repo}", sitename="github",
                    date=(info.get("pushed_at") or "")[:10], partial=partial)


async def gitee_api(rt, url, lang):
    """a gitee repository (its facts and readme) or one file, through gitee's
    public api (no login)."""
    owner, repo, ref, path = _gitee_parts(urlsplit(url)) or ("", "", "", "")
    api = f"https://gitee.com/api/v5/repos/{owner}/{repo}"
    if path:
        item = await _json(rt, f"{api}/contents/{quote(path)}", {"ref": ref})
        return api_page(url, f"{api}/contents/{path}?ref={ref}", _base64_text(item),
                        [f"the file {path} at {ref}, read through gitee's api"], title=f"{owner}/{repo}: {path}", sitename="gitee")
    info = await _json(rt, api)
    facts = [info.get("description") or "", f"★{info.get('stargazers_count', 0)}", info.get("language") or "",
             f"updated {(info.get('updated_at') or '')[:10]}", f"license {info.get('license')}" if info.get("license") else ""]
    readme, note, partial = await _readme(rt, f"{api}/readme", "gitee")
    return api_page(url, api, " · ".join(f for f in facts if f) + ("\n\n" + readme if readme else ""), [note],
                    title=info.get("full_name") or f"{owner}/{repo}", sitename="gitee",
                    date=(info.get("updated_at") or "")[:10], partial=partial)


# -- naver blog, crates.io, pubmed ----------------------------------------------

_NAVER_BLOG_POST = re.compile(r"^/([A-Za-z0-9_-]+)/(\d+)/?$")


def _naver_blog_post(parts):
    """(blog, post number) of a naver blog post url, either spelling, else None."""
    m = _NAVER_BLOG_POST.match(parts.path)
    if m:
        return m.groups()
    query = parse_qs(parts.query)
    blog, post = query.get("blogId", [""])[0], query.get("logNo", [""])[0]
    return (blog, post) if blog and post.isdigit() else None


# a crate, at a version or not, and its tabs (/versions, /dependencies),
# which are read as the crate: a tab name is no version
_CRATE = re.compile(r"^/crates/([A-Za-z0-9_-]+)(?:/(\d[^/]*))?(?:/[a-z_]+)?/?$")


async def crates_page(rt, url, lang):
    """a crate's facts and readme through crates.io's api, which asks to be
    called with a user-agent that names the client (engines/crates.py)."""
    name, version = _CRATE.match(urlsplit(url).path).groups()
    api = f"https://crates.io/api/v1/crates/{name}"
    headers = {"User-Agent": crates.USER_AGENT}
    crate = (await _json(rt, api, {"include": "default_version"}, headers=headers)).get("crate") or {}
    version = version or crate.get("default_version") or crate.get("max_version") or ""
    facts = [crate.get("description") or "", f"v{version}" if version else "",
             f"{crate.get('downloads') or 0:,} downloads", crate.get("repository") or "",
             f"updated {(crate.get('updated_at') or '')[:10]}"]
    try:
        # asked for json, the readme endpoint answers with where the
        # rendered readme lives
        where = (await _json(rt, f"{api}/{version}/readme", headers=headers, spaced=False)).get("url")
        if not where:
            raise ApiError("the readme answer names no url")
        readme = visible_text(await _text(rt, where, headers=headers, spaced=False))
        note, partial = "the crate's facts and readme, read through crates.io's api", False
    except ApiError as e:
        readme, note, partial = "", f"the crate's facts through crates.io's api; no readme ({e})", e.status != 404
    except RequestException as e:
        readme, note, partial = "", f"the crate's facts through crates.io's api; the readme could not be read ({net.describe(e)})", True
    return api_page(url, api, " · ".join(" ".join(f.split()) for f in facts if f) + ("\n\n" + readme if readme else ""),
                    [note], title=f"{name} {version}".strip(), sitename="crates.io",
                    date=(crate.get("updated_at") or "")[:10], partial=partial)


_PUBMED_ID = re.compile(r"^/(\d+)/?$")


async def pubmed_abstract(rt, url, lang):
    """a pubmed record as text through ncbi's e-utilities: citation, title,
    authors, abstract."""
    pmid = _PUBMED_ID.match(urlsplit(url).path).group(1)
    api = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    text = (await _text(rt, api, {"db": "pubmed", "id": pmid, "rettype": "abstract", "retmode": "text"})).strip()
    # the record opens with its citation, then the title, each a paragraph
    paragraphs = [" ".join(p.split()) for p in text.split("\n\n")]
    return api_page(url, f"{api}?db=pubmed&id={pmid}&rettype=abstract", text,
                    ["read through ncbi's e-utilities: the citation and abstract, not the full paper"],
                    title=paragraphs[1] if len(paragraphs) > 1 else "", sitename="pubmed")


# -- amazon, airbnb ----------------------------------------------------------

_AMAZON_HOST = re.compile(r"^(?:www\.)?amazon\.(?:com|de|co\.uk|fr|it|es|nl|se|pl|com\.be|com\.tr|co\.jp|ca|com\.mx|"
                          r"com\.br|com\.au|in|ae|sa|sg|eg)$")
_AMAZON_ASIN = re.compile(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:[/?]|$)")
_AIRBNB_HOST = re.compile(r"^(?:www\.)?airbnb\.(?:com|[a-z]{2}|co\.[a-z]{2}|com\.[a-z]{2})$")
_AIRBNB_ROOM = re.compile(r"^/rooms/(\d+)")


def amazon_asin(url):
    """(store, asin) of an amazon product url, else None. the store is always
    https://www.amazon.<tld>/, the form the engine's session knows: a link
    without www. crashed it (found in review 2026-10-03)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    found = _AMAZON_ASIN.search(parts.path) if _AMAZON_HOST.match(host) else None
    return (f"https://www.{host.removeprefix('www.')}/", found.group(1)) if found else None


def airbnb_room_url(url):
    """the room page an airbnb room url stands for, else None: the room id and
    the url's query on airbnb.com, whose rooms are the same on every airbnb
    domain. the url itself is never requested: this route reads through the
    engine's session, which fetch's vetting does not guard, and
    http://airbnb.localhost:6379/rooms/1 must not lead off the public web
    (found in review 2026-10-03)."""
    parts = urlsplit(url)
    if not _AIRBNB_HOST.match((parts.hostname or "").lower()):
        return None
    room = _AIRBNB_ROOM.match(parts.path)
    return f"{airbnb.HOME}rooms/{room.group(1)}" + (f"?{parts.query}" if parts.query else "") if room else None


async def amazon_product(rt, url, lang):
    store, asin = amazon_asin(url)
    title, text = await amazon.product(rt.http, f"{store}dp/{asin}", lang)
    if not title:
        raise ApiError("amazon showed no product: an interstitial, or the product is gone")
    return api_page(url, f"{store}dp/{asin}", text,
                    ["read through weltsuche's amazon session: price, rating, availability, bullet points, description "
                     "and details as amazon shows them to a visitor of this store"],
                    title=title, sitename=store.split("www.")[-1].strip("/"))


async def airbnb_room(rt, url, lang):
    room = airbnb_room_url(url)
    resp = await rt.http.get(airbnb.NAME, room, lang=lang, referer=airbnb.HOME)
    try:
        title, text = airbnb.room_text(resp.text)
    except ParseError as e:
        # the page's data changed shape: a route's failure, not a weltsuche bug
        raise ApiError(f"the room page's data cannot be read: {e}") from e
    if not title:
        raise ApiError("the room page carries no listing data: the room is gone, or the page changed")
    return api_page(url, room, text, ["read from the room page's own data: facts, ratings and description; "
                                      "the price for given dates is in the search results"],
                    title=title, sitename="airbnb")


# -- the table ----------------------------------------------------------------

def _same(parts):
    return parts.path + (f"?{parts.query}" if parts.query else "")


def _identity(parts):
    return parts.geturl()


SITES = (
    Site("reddit", ("reddit.com",), (
        Frontend("redlib", _same),
        Api("arctic shift archive", reddit_arctic, applies=lambda p: _reddit_post(p) is not None),
        Api("pullpush archive", reddit_pullpush, applies=lambda p: _reddit_post(p) is not None),
    )),
    Site("x", ("x.com", "twitter.com", "fixupx.com", "fxtwitter.com", "vxtwitter.com", "nitter.net"), (
        Api("fxtwitter api", x_fxtwitter_status, applies=lambda p: _x_status(p) is not None),
        Frontend("nitter", lambda p: p.path),
        Api("fxtwitter api", x_fxtwitter_profile, applies=lambda p: _x_profile(p) is not None),
    )),
    Site("youtube", ("youtube.com", "youtu.be"), (
        Api("youtube player api", youtube_player, applies=lambda p: youtube_video(p) is not None),
        Frontend("invidious", _invidious_path),
    ), words_in_text=False),
    Site("instagram", ("instagram.com",), (
        Frontend("kittygram", _same),
        Load("instagram embed", _instagram_embed, applies=lambda p: _INSTAGRAM_POST.match(p.path) is not None, short=True),
    )),
    Site("tiktok", ("tiktok.com",), (
        Api("tiktok oembed", tiktok_oembed, applies=_tiktok_video),
    )),
    Site("threads", ("threads.net", "threads.com"), (
        Load("threads embed", lambda p: f"https://www.threads.com{p.path.rstrip('/')}/embed",
             applies=lambda p: _THREADS_POST.match(p.path) is not None, short=True),
    )),
    Site("telegram", ("t.me", "telegram.me"), (
        Load("telegram embed", _telegram_embed, applies=lambda p: _TELEGRAM_POST.match(p.path) is not None, short=True),
    )),
    Site("bluesky", ("bsky.app",), (
        Api("bluesky api", bluesky_thread, applies=lambda p: _BLUESKY_POST.match(p.path) is not None),
    )),
    Site("medium", ("medium.com",), (
        Load("medium itself", _identity, thin_is_weak=True),
        Frontend("libMedium", _same),
    )),
    Site("stack exchange", (), (
        Api("stack exchange api", stackexchange),
    ), match=lambda url: stackexchange_target(url) is not None),
    Site("code file", (), (
        Load("raw file", lambda p: raw_file_url(p.geturl())),
    ), match=lambda url: raw_file_url(url) is not None),
    Site("github repository", (), (
        Api("github api", github_repo),
        Load("github itself", _identity),
    ), match=lambda url: _github_repo(urlsplit(url)) is not None),
    Site("gitee", ("gitee.com",), (
        Api("gitee api", gitee_api, applies=lambda p: _gitee_parts(p) is not None),
    )),
    Site("naver blog", ("blog.naver.com",), (
        Load("naver blog mobile page", lambda p: "https://m.blog.naver.com/{}/{}".format(*_naver_blog_post(p)),
             applies=lambda p: _naver_blog_post(p) is not None),
    )),
    Site("crates.io", ("crates.io",), (
        Api("crates.io api", crates_page, applies=lambda p: _CRATE.match(p.path) is not None),
    )),
    Site("amazon product", (), (
        Api("amazon product page", amazon_product),
    ), words_in_text=False, match=lambda url: amazon_asin(url) is not None),
    Site("airbnb room", (), (
        Api("airbnb room data", airbnb_room),
    ), words_in_text=False, match=lambda url: airbnb_room_url(url) is not None),
    Site("pubmed", ("pubmed.ncbi.nlm.nih.gov",), (
        Api("ncbi e-utilities", pubmed_abstract, applies=lambda p: _PUBMED_ID.match(p.path) is not None),
    )),
)

# host prefixes that pick a variant of the same site
_VARIANTS = ("www.", "m.", "mobile.", "old.", "new.", "np.")


def _host(url):
    host = (urlsplit(url).hostname or "").lower()
    for prefix in _VARIANTS:
        if host.startswith(prefix):
            return host[len(prefix):]
    return host


def match(url):
    """the site `url` belongs to, or None."""
    host = _host(url)
    return next((s for s in SITES if (s.match(url) if s.match else host in s.hosts)), None)


def applicable(site, url):
    """the site's routes that can read this kind of url, in order."""
    parts = urlsplit(url)
    return [route for route in site.routes if route.applies(parts)]
