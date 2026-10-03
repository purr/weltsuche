"""fan a query out to several engines, merge and rank what comes back."""

import asyncio
import logging
from urllib.parse import parse_qsl, urlencode, urlsplit

from . import engines, net
from .models import SearchReply

log = logging.getLogger("weltsuche.search")

_TRACKING = ("utm_", "fbclid", "gclid", "yclid", "mc_cid", "mc_eid", "_ga", "ref_src", "spm", "srsltid")

# host labels that only pick a variant of the same page (www., the mobile
# site). dropped anywhere left of the registrable name, so post.m.smzdm.com
# and post.smzdm.com, which both engines listed for one article, merge.
_VARIANT_LABELS = ("www", "m", "mobile")


def normalize_url(url):
    """the same page under two spellings is one hit."""
    try:
        p = urlsplit(url)
    except ValueError:
        return url
    labels = p.netloc.lower().split(".")
    host = ".".join(label for i, label in enumerate(labels)
                    if not (label in _VARIANT_LABELS and len(labels) - i > 2))
    path = p.path.rstrip("/") or "/"
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith(_TRACKING)]
    return host + path + ("?" + urlencode(sorted(query)) if query else "")


def pick_engines(config, lang, engines_arg):
    """the engines of one search: the caller's names, where a set name
    (ENGINE_SETS) stands for its engines in `lang`; without names, the
    language's defaults. order kept, repeats dropped."""
    out = []
    for given in engines_arg or []:
        name = (given or "").strip().lower()
        members = config.ENGINE_SETS.get(name)
        for engine in (members.get(lang) or members["*"]) if members is not None else [name]:
            if engine and engine not in out:
                out.append(engine)
    # no names, or only empty ones: the language's defaults
    return out or list(config.ENGINES_BY_LANG.get(lang) or config.ENGINES_BY_LANG["*"])


def parse_lang(config, lang, region):
    """(lang, region) from what the caller passed. a tag like "zh-TW",
    "zh-Hant-HK" or "pt_BR" gives both: the language, and the region when
    none was passed, so traditional-chinese sources are not searched from
    mainland china; "zh-Hant" alone means taiwan. three-letter languages
    ("fil", "haw") stay whole: cut to two letters they named another
    language. a language REGION_BY_LANG does not know, or a region that is
    no country code ("all"), gives no region (""), which the engines read as
    "any country"."""
    subtags = (lang or config.DEFAULT_LANG).strip().lower().replace("_", "-").split("-")
    code = subtags[0][:3] or config.DEFAULT_LANG
    if not region:
        region = next((s for s in subtags[1:] if len(s) == 2 and s.isalpha()), "")
        if not region and "hant" in subtags[1:]:
            region = "tw"
    region = (region or config.REGION_BY_LANG.get(code) or "").strip().lower()
    return code, region if len(region) == 2 and region.isalpha() else ""


def merge(per_engine, max_results, weights=None):
    """per_engine: list of (engine_name, [Hit]). reciprocal-rank fusion with a
    bonus for agreement between engines; `weights` scales an engine's hits.
    returns new Hit objects with `engines` set, best first."""
    weights = weights or {}
    merged, scores = {}, {}
    for name, hits in per_engine:
        weight = weights.get(name, 1.0)
        for rank, hit in enumerate(hits):
            key = normalize_url(hit.url)
            entry = merged.get(key)
            if entry is None:
                entry = merged[key] = hit.model_copy(update={"engines": []})
                scores[key] = 0.0
            scores[key] += weight / (rank + 1)
            if name not in entry.engines:
                entry.engines.append(name)
            if len(hit.snippet) > len(entry.snippet):
                entry.snippet = hit.snippet
            if not entry.title and hit.title:
                entry.title = hit.title
    order = sorted(merged, key=lambda k: scores[k] * (1.0 + 0.5 * (len(merged[k].engines) - 1)), reverse=True)
    return [merged[k] for k in order[:max_results]]


async def search(rt, query, lang=None, region=None, engines_arg=None, max_results=None, params=None):
    config = rt.config
    lang, region = parse_lang(config, lang, region)
    if not query.strip():
        # sent on, an empty query came back from the engines as drift or as
        # their errors
        return SearchReply(query=query, lang=lang, region=region, results=[], engines=[],
                           summary="empty query: nothing was searched")
    names = pick_engines(config, lang, engines_arg)
    n_each = max(1, int(config.PER_ENGINE_RESULTS))
    max_results = max(1, int(max_results or config.MAX_RESULTS))
    weights = {name: getattr(mod, "WEIGHT", 1.0) for name, mod in engines.MODULES.items()}

    results = await asyncio.gather(*(
        engines.run(name, rt.http, rt.breakers, rt.cache, config, query, lang, region, n_each, params)
        for name in names
    ))
    per_engine = [(status.engine, hits) for hits, status in results]
    statuses = [status for _, status in results]
    merged = merge(per_engine, max_results, weights)

    answered = [s.engine for s in statuses if s.ok and s.count]
    # names only: why each engine did not answer is in its status already
    empty = [s.engine for s in statuses if s.ok and not s.count]
    failed = [s.engine for s in statuses if not s.ok]
    return SearchReply(
        query=query, lang=lang, region=region, results=merged, engines=statuses,
        summary=f"{len(merged)} results from {', '.join(answered) or 'no engine'}"
                + (f"; nothing found by {', '.join(empty)}" if empty else "")
                + (f"; no answer from {', '.join(failed)} (see engines)" if failed else ""),
    )


async def search_many(rt, queries, concurrency=3):
    """queries: server.Query objects. run `concurrency` at a time; engines
    within one query run in parallel anyway."""
    sem = asyncio.Semaphore(concurrency)

    async def one(q):
        async with sem:
            try:
                return await search(rt, q.query, q.lang, q.region, q.engines, q.max_results, q.params)
            except Exception as e:  # one query's failure must not lose the others' answers; logged with its traceback
                log.exception("search_many: %r", q.query)
                return SearchReply(query=q.query, lang=q.lang or "", region=q.region or "", results=[], engines=[],
                                   summary=f"internal error, a weltsuche bug (the traceback is in weltsuche.log): {net.describe(e)}")

    return await asyncio.gather(*(one(q) for q in queries))
