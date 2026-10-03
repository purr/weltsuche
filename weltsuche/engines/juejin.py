"""juejin (掘金), china's developer articles, through the search api its own
site uses (no login)."""

from .common import QueryError, api_json

NAME = "juejin"
HOME = "https://juejin.cn/"


async def search(http, query, lang, region, n):
    body = {"key_word": query, "id_type": 0, "cursor": "0", "limit": n, "search_type": 0, "sort_type": 0, "version": 1}
    data = await api_json(http, NAME, "https://api.juejin.cn/search_api/v1/search", query, lang="zh",
                          method="POST", json_body=body)
    if data.get("err_no"):
        # juejin reports a failure at http 200, as err_no and err_msg
        raise QueryError(f"juejin: {data.get('err_msg') or data['err_no']}")
    hits = []
    for entry in data.get("data") or []:
        model = entry.get("result_model") or {}
        article = model.get("article_info")
        if not article:
            continue  # tags, users and courses share the result list
        author = (model.get("author_user_info") or {}).get("user_name", "")
        facts = [" ".join((article.get("brief_content") or "").split())[:200],
                 f"{article.get('view_count', 0)} views", f"{article.get('digg_count', 0)} likes", f"by {author}" if author else ""]
        hits.append({"title": article.get("title") or "", "url": f"https://juejin.cn/post/{article.get('article_id')}",
                     "snippet": " · ".join(f for f in facts if f)})
    return hits
