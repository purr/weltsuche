"""hacker news through algolia's public search api: stories people build and
discuss. a hit links to the discussion; the snippet names the story's link."""

from .common import api_json

NAME = "hackernews"
HOME = "https://news.ycombinator.com/"


async def search(http, query, lang, region, n):
    data = await api_json(http, NAME, "https://hn.algolia.com/api/v1/search", query,
                          params={"query": query, "tags": "story", "hitsPerPage": n})
    hits = []
    for story in data.get("hits") or []:
        facts = [f"{story.get('points', 0)} points", f"{story.get('num_comments', 0)} comments",
                 (story.get("created_at") or "")[:10]]
        if story.get("url"):
            facts.append(f"links to {story['url']}")
        hits.append({"title": story.get("title") or "", "url": f"https://news.ycombinator.com/item?id={story.get('objectID')}",
                     "snippet": " · ".join(f for f in facts if f)})
    return hits
