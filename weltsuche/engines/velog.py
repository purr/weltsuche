"""velog, korea's developer blog platform, through its public graphql api
(no login)."""

from urllib.parse import quote

from .common import QueryError, api_json

NAME = "velog"
HOME = "https://velog.io/"
QUERY = ("query($k: String!, $n: Int) { searchPosts(keyword: $k, limit: $n) "
         "{ posts { title url_slug short_description released_at likes user { username } } } }")


async def search(http, query, lang, region, n):
    data = await api_json(http, NAME, "https://v2.velog.io/graphql", query, lang="ko", method="POST",
                          json_body={"query": QUERY, "variables": {"k": query, "n": n}})
    if data.get("errors"):
        # graphql reports a failure at http 200, in `errors`
        raise QueryError(f"velog: {(data['errors'][0] or {}).get('message', 'an error')}")
    hits = []
    for post in ((data.get("data") or {}).get("searchPosts") or {}).get("posts") or []:
        user = (post.get("user") or {}).get("username", "")
        facts = [post.get("short_description") or "", f"{post.get('likes', 0)} likes", (post.get("released_at") or "")[:10]]
        hits.append({"title": post.get("title") or "", "url": f"https://velog.io/@{user}/{quote(post.get('url_slug') or '')}",
                     "snippet": " · ".join(f for f in facts if f)})
    return hits
