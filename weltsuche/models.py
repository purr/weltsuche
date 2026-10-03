"""the shapes weltsuche answers and stores in. one definition each: the
engines, the merge, the page readers, the cache, the cli and the mcp tools all
use these, so a field means the same thing everywhere.

replies are sent without fields that still hold their default (an empty
author, no notes), which keeps them short; required fields are always sent.
"""

from pydantic import BaseModel, Field


class Query(BaseModel):
    """one search, as the search and search_many tools take it."""
    query: str = Field(description="the search text, written in the language of `lang`")
    lang: str | None = Field(None, description="iso 639-1 language of the query and wanted results; a tag like zh-TW also sets the region; the server's DEFAULT_LANG when omitted")
    region: str | None = Field(None, description="iso 3166-1 alpha-2 country, defaults from lang")
    engines: list[str] | None = Field(None, description="engine names or engine set names (the search tool lists both) to use instead of the default engines for the language")
    max_results: int | None = Field(None, ge=1, le=30, description="merged results to return; the server's MAX_RESULTS when omitted")
    params: dict[str, str | int | float | bool] | None = Field(None, description="filters for the engines that take them (the search tool lists them per engine: prices, sort, condition, sold, place, dates, guests); other engines ignore them")


class FetchRequest(BaseModel):
    """one fetch, as the fetch_many tool takes it."""
    url: str = Field(description="the address to read")
    lang: str | None = Field(None, description="accept-language, and a youtube transcript's language; guessed from the domain when omitted")
    max_chars: int | None = Field(None, description="characters to show; lower by default the more urls "
                                  "are asked for in one call, so the combined reply stays well under the "
                                  "server's output limit")
    offset: int = Field(0, description="where in the text to start")
    raw: bool = Field(False, description="the html source instead of the extracted text")
    focus: str | None = Field(None, description="a few words (in the page's language) of what you look for; only the matching passages come back")


class Hit(BaseModel):
    """one search result. `engines` is set by the merge; the order of a
    reply's results is their rank."""
    title: str
    url: str
    snippet: str = ""
    engines: list[str] = Field(default_factory=list)


class EngineStatus(BaseModel):
    """what one engine did for one query: answered (`ok`, `count`), came from
    the cache, or why it did not answer (`note`)."""
    engine: str
    ok: bool
    count: int
    cached: bool = False
    note: str = ""


class SearchReply(BaseModel):
    query: str
    lang: str
    region: str
    results: list[Hit]
    engines: list[EngineStatus]
    summary: str


class Page(BaseModel):
    """one read page, or why it could not be read (`error`).

    `url` is the address to cite. `via` is the page or api actually read when
    it differs (a frontend, an archive, an embed, a redirect). `notes` say how
    the text was obtained and what is missing from it. `tried` lists the
    routes that failed before this answer, each with its reason, so a
    degraded answer never looks like a clean one. `partial`: the answer is
    incomplete for a reason that may pass (a download that ran out of time, a
    side request that failed, a thin fallback); it is not cached, so a later
    try reads again.
    """
    url: str
    via: str = ""
    status: int | None = None
    content_type: str = ""
    kind: str = ""
    title: str = ""
    author: str = ""
    date: str = ""
    sitename: str = ""
    description: str = ""
    notes: list[str] = Field(default_factory=list)
    tried: list[str] = Field(default_factory=list)
    error: str = ""
    partial: bool = False
    text: str = ""

    def with_note(self, *notes):
        return self.model_copy(update={"notes": [*self.notes, *(n for n in notes if n)]})


class FetchReply(Page):
    """a page as the fetch tool returns it: one slice of the text. `cached`:
    read from the cache (CACHE_TTL_S), not from the site just now."""
    cached: bool = False
    chars_total: int
    offset: int
    chars_returned: int
    truncated: bool
    next_offset: int | None = None


class BreakerStatus(BaseModel):
    """a bucket's breaker as engines_status and --status show it."""
    open: bool
    reason: str = ""
    seconds_left: int = 0
    last_request_ago_s: int | None = None


class StatusReply(BaseModel):
    engines: list[str]
    breakers: dict[str, BreakerStatus]
    cache: dict[str, int]


class BucketState(BaseModel):
    """spacing and breaker state of one bucket (an engine, a host, a frontend
    instance), shared by every weltsuche process through state.json in the data folder."""
    open_until: float = 0.0
    reason: str = ""
    last_request: float = 0.0


class StateFile(BaseModel):
    version: int
    engines: dict[str, BucketState] = Field(default_factory=dict)


def reply_json(reply):
    """the compact json an mcp tool returns (a model or a list of them):
    defaults left out, no indent."""
    if isinstance(reply, list):
        return "[" + ",".join(r.model_dump_json(exclude_defaults=True) for r in reply) + "]"
    return reply.model_dump_json(exclude_defaults=True)
