"""naver blog, where korean reviews, buying advice and experience reports
live, through naver's blog results tab. same page family as the web tab:
see naver.py for how it is read."""

import functools

from .common import settle
from .naver import HOME, URL
# the same site as the web tab: spaced and rested with it, so a korean
# search does not fire two requests at search.naver.com at once, and a block
# one of them meets rests both
from .naver import NAME as BREAKER
from .naver import parse as _parse

NAME = "naver_blog"

parse = functools.partial(_parse, target=".nblg")


async def search(http, query, lang, region, n):
    resp = await http.get(NAME, URL, lang="ko", referer=HOME, params={"ssc": "tab.blog.all", "query": query},
                          check=False, bucket=BREAKER)
    return settle(resp, parse, query)[:n]
