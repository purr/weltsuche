"""the mcp server: five tools over stdio.

stdout is the protocol channel, so the very first thing `serve()` does is move
`sys.stdout` to stderr for everything in this process and hand the real stdout
to the transport explicitly. a stray print from any library then lands in the
log, never in the middle of a json-rpc frame.

tool calls run concurrently (the sdk starts a task per request), so the
model can fetch several pages at once. every tool answers with a model from
models.py; an unexpected exception becomes an answer that names it, with the
traceback in weltsuche.log in the data folder, never a bare "error executing tool".
"""

import copy
import functools
import inspect
import json
import logging
import sys
from io import TextIOWrapper

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.stdio import stdio_server
from . import engines as engines_mod
from . import fetch as fetch_mod
from . import search as search_mod
from .engines.common import describe_params
from .models import FetchRequest, Query, StatusReply, reply_json

log = logging.getLogger("weltsuche.server")

INSTRUCTIONS = """weltsuche: web and code search in every language, and page reading that gets
through where plain clients are walled off.

use it on your own, without being asked, whenever a task depends on facts
outside this repository that memory may have wrong or stale: a product, a
price, a medical or scientific claim, what people report, a library's current
api or version, an error message from a dependency, a cve. size the effort to
the need: a fact takes one search and maybe one page; comparisons, "what do
people say" and topics that live in another country take several languages
and 3 to 6 pages. the weltsuche skill has the full method.

- translate yourself: write each query natively in its language with the
  matching `lang` (audio and hardware: zh, ja, de; medicine: zh, ru, de;
  security: ru, zh) and run the languages at once with search_many, with your
  built-in web search alongside for english.
- engine sets: `engines: ["code"]` finds repositories, `["codesearch"]`
  exact code, `["dev"]` what developers write in the query's language,
  `["packages"]` npm and crates.io, `["science"]` pubmed and arxiv,
  `["shopping"]` amazon, ebay, kleinanzeigen (and `airbnb`) with `params`
  for prices, sort, place, dates. these match keywords, not sentences. run code and dev in english
  and chinese at least. inside one known github repository the github cli
  (`gh api repos/OWNER/REPO/...`) is faster than searching.
- read several pages with one `fetch_many` call, not one `fetch` per page,
  and quote them with url and language. for one fact in a long page or
  pdf, pass `focus` with a few words: only the matching passages come back.
  a github repository url comes back as its facts and readme, a file link
  as the raw file.
- a resting engine is normal; the others carry the query.
- weltsuche is an aid, not a boundary: while a question is open, also use
  WebSearch, WebFetch, the github cli, other tools of the session, and sites
  with no engine here (`site:`, or fetch the site's own search page)."""


def _described(name):
    """a tool parameter defined once, in models.Query: its default, bounds
    and description."""
    return copy.copy(Query.model_fields[name])


async def _answer(tool, work):
    """the tool's reply as compact json. fastmcp would turn an exception into
    a bare str(e) and log nothing; here it becomes an answer that names the
    tool and the failure, and the traceback goes to the log."""
    try:
        return reply_json(await work)
    except Exception as e:  # every call gets an answer; the traceback is logged
        log.exception("tool %s failed", tool)
        return json.dumps({"error": f"weltsuche {tool}: internal {type(e).__name__}: {e} (a weltsuche bug; the traceback is in weltsuche.log in the data folder)"})


def build(rt):
    mcp = FastMCP("weltsuche", instructions=INSTRUCTIONS, log_level="WARNING")
    # text content only. by default fastmcp gives a tool returning str the
    # output schema {"result": string} and sends the reply a second time as
    # structured content, which claude code shows the model instead: the
    # compact json arrived as a string inside json, every quote escaped
    # (2026-10-03)
    tool = functools.partial(mcp.tool, structured_output=False)
    known = ", ".join(engines_mod.names(rt.config))
    filters = "; ".join(f"{name}: {describe_params(mod.PARAMS)}" for name, mod in engines_mod.MODULES.items()
                        if hasattr(mod, "PARAMS"))
    sets = "; ".join(f"{name}: " + ", ".join(sorted({e for members in by_lang.values() for e in members}))
                     for name, by_lang in rt.config.ENGINE_SETS.items())

    @tool(description=inspect.cleandoc(f"""search the web in a given language across several engines at once.

        `query` must be written in the language of `lang` (write chinese for
        lang="zh", russian for "ru", hebrew for "he"; "zh-TW" also picks the
        region). results are merged and deduplicated across engines; a hit
        listed by several engines ranks higher. `engines` overrides the
        default set for the language; known names: {known}. a set name in
        `engines` stands for the set's engines in `lang` ({sets}). `params`
        filters the engines that take them, others ignore them: {filters}."""))
    async def search(
        query: str = _described("query"),
        lang: str | None = _described("lang"),
        region: str | None = _described("region"),
        engines: list[str] | None = _described("engines"),
        max_results: int | None = _described("max_results"),
        params: dict[str, str | int | float | bool] | None = _described("params"),
    ) -> str:
        return await _answer("search", search_mod.search(rt, query, lang, region, engines, max_results, params))

    @tool()
    async def search_many(queries: list[Query]) -> str:
        """run several searches in parallel, typically the same question in
        different languages. each entry is a full search call."""
        return await _answer("search_many", search_mod.search_many(rt, queries))

    @tool()
    async def fetch(
        url: str,
        lang: str | None = None,
        max_chars: int | None = None,
        offset: int = 0,
        raw: bool = False,
        focus: str | None = None,
    ) -> str:
        """read a page with a browser fingerprint and return its text (article
        body, tables and code kept, navigation dropped) with title, author,
        date and the page's own description. pdfs are read too. several urls
        already in hand (search hits, links on a page): fetch_many, one call,
        not this tool called several times. long pages are
        paged: the reply carries `next_offset`, pass it as `offset` to
        continue; `max_chars` defaults to the server's FETCH_MAX_CHARS.
        `focus` (a few words of what you look for, in the page's language)
        returns only the passages that mention them, each marked with its
        offset, instead of the first slice: the cheap way to read a long page
        or pdf for one fact.
        `raw=true` returns the html source instead. `lang` sets
        accept-language (and a youtube transcript's language); guessed from
        the domain when omitted. only public http(s) pages are read: file://
        urls and addresses on this machine or the local network are refused.
        reddit, x, youtube, instagram, tiktok, threads, bluesky, medium and
        stack exchange urls are read through apis, embeds or public frontends;
        `url` stays the original to cite, `via` names what was read. `error`
        says why a page could not be read (a bot wall is an error, never the
        page's text), `notes` what the text is and lacks, `tried` the routes
        that failed first.
        """
        return await _answer("fetch", fetch_mod.fetch(rt, url, lang, max_chars, offset, raw, focus))

    # the declared limit keeps a reply of several pages inline: claude code
    # saves an undeclared tool's reply to a file past 50,000 characters (see
    # FETCH_MANY_REPLY_MAX_CHARS)
    @tool(meta={"anthropic/maxResultSizeChars": fetch_mod.FETCH_MANY_REPLY_MAX_CHARS})
    async def fetch_many(requests: list[FetchRequest]) -> str:
        """read several pages in one call: the way to fetch urls already in
        hand (search hits, links found on a page), instead of calling fetch
        once per url. each entry is a full fetch call (url, lang, max_chars,
        offset, raw, focus); three of them run at a time. a request
        without its own max_chars gets fetch's default at most, and a smaller
        share of the page the more urls are asked for together, so the
        combined reply arrives in one piece; pass focus on each to cut that
        further. one url's failure (a wall, a network error, a weltsuche
        bug) never drops the others' pages."""
        return await _answer("fetch_many", fetch_mod.fetch_many(rt, requests))

    @tool()
    async def engines_status() -> str:
        """which engines exist, which are resting after a block or rate
        limit and for how long, and the size of the result cache."""
        async def status():
            return StatusReply(engines=engines_mod.names(rt.config), breakers=await rt.engine_breakers(),
                               cache=await rt.cache.stats())
        return await _answer("engines_status", status())

    return mcp


async def serve(rt):
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    # same wrapping the sdk does itself (mcp/server/stdio.py), just on the
    # handle we saved before moving sys.stdout away
    stdout = anyio.wrap_file(TextIOWrapper(real_stdout.buffer, encoding="utf-8"))
    mcp = build(rt)
    server = mcp._mcp_server  # noqa: SLF001 - run_stdio_async() offers no way to pass our own stdout
    log.info("weltsuche mcp on stdio, engines: %s", ", ".join(engines_mod.names(rt.config)))
    try:
        async with stdio_server(stdout=stdout) as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        try:
            await rt.close()
        except Exception:  # a failed close must not replace the error that ended the server; logged
            log.exception("closing the sessions failed")
        log.info("weltsuche stopped")
