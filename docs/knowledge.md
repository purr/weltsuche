# weltsuche: what we know

maintainer notes. the readme says how to install and use weltsuche; this file
says why it works the way it does: what every engine and site does to a plain
client, what was tried and dropped, which decisions were made and why, the
gotchas that cost time, and what is still open.

everything here was measured from one connection between 2026-09-27 and
2026-10-03. sites change; the dates matter.
when a number here and the code disagree, the code is newer.

## 1. goals

- an english question in, sources from every relevant language out, and the
  pages actually read, not just their snippets.
- claude does the translating: it writes each query natively. the tool never
  translates (a translation api would need a key, a dependency and another
  rate limit, and mangles jargon and brand names such as 拓品 for topping).
- one client that looks like one person at a browser: curl_cffi chrome
  impersonation, a persistent cookie jar, spacing and circuit breakers. no
  proxy rotation, no captcha solving, no bypassing of javascript bot checks.
  a proof-of-work toll (anubis) asks for compute, not for a human, and is paid.
- run next to claude code's own WebSearch, not instead of it: an english
  question to WebSearch returns english, mostly us sources; weltsuche's
  english engines rest now and then after rate limits; each covers the
  other's gaps (WebSearch cannot reach reddit at all).
- depth is decided per question (the skill's step 0): quick questions stay
  quick; the full multilingual method only when it earns its time.

## 2. decisions

| decision | why | when |
|---|---|---|
| a claude code plugin (skill + mcp server), installed from this repo as its own marketplace | the documented way to ship an mcp server and a skill together; one install, loaded in place from the checkout, edits live after `/reload-plugins` | 2026-09-29 |
| uv runs the server (`uv run --directory ${CLAUDE_PLUGIN_ROOT} ... python -m weltsuche serve`) | claude code documents no dependency step for plugins; uv is the python mcp standard, builds the venv and even fetches python; replaces the earlier self-bootstrapping `main.py` launcher | 2026-09-29 |
| venv, config, state, cache, cookies and log in `${CLAUDE_PLUGIN_DATA}` | the plugin root is replaced on update; the data folder survives it | 2026-09-29 |
| `config.py` in the data folder, seeded from `config.example.py` with every setting commented out; a key not set keeps the example's current default | updates that add or change defaults need no edit (the first seeds were full copies: they set every key, and korean searches never got naver); there are no secrets, so seeding does not stop to ask | 2026-09-29, seed commented 2026-10-03 |
| no environment variables for configuration | one config file is the only place a setting lives; `UV_PROJECT_ENVIRONMENT` in `plugin.json` is uv's own plumbing, not a setting | 2026-09-27 |
| the mcp server is declared inline in `plugin.json` (`mcpServers`), not in a root `.mcp.json` | a root `.mcp.json` is also the project-scope mcp config of anyone working in this checkout: claude code asked to approve a second `weltsuche` server whose `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_DATA}` were unset, and it failed to connect | 2026-10-03 |
| every reply and state shape is a pydantic model (`models.py`), sent without default-valued fields | one definition per shape; shorter replies | 2026-09-29 |
| walled sites are read through routes raced in parallel: next route on failure or after 6 s, at most 2 at once, first good answer wins, failures listed in `tried` | redundancy without hammering; a degraded answer never looks clean | 2026-09-29 |
| reddit's javascript bot check is not answered; reddit is read through redlib and archives | the project does not bypass bot checks; public frontends and archives are what people use instead | 2026-09-29 |
| `redlib.catsarch.com` is the first redlib instance (`FRONTENDS_PREFERRED`) | it answered when most redlib instances did not; libredirect's list follows it | 2026-09-29 |
| frontend instances come from libredirect's daily `data.json`, never hard-coded | instances die weekly (anonymousoverflow: 1 of 34 alive) | 2026-09-29 |
| `fetch` reads only public http(s) pages | a fetched page can inject "fetch file:///..." or a lan address; the first version read local files | 2026-09-29 |
| failures are never cached | a wall or a network error cached for a day hides the page from every later try | 2026-09-29 |
| spacing per engine for search, per host for fetch; the slot is reserved in `state.json` under a lock file | several claude code sessions share one ip and must not fire together | 2026-09-29 |
| wikipedia hits count half in the merge | its full-text search matches any article that mentions a query word | 2026-09-29 |
| refusal phrases only count on a results page that yielded no hits | a snippet quoting "captcha" parked wikipedia for an hour | 2026-09-29 |
| committed fixtures are scrubbed of cookies, tokens and the client ip | they are captures of real sessions | 2026-09-29 |
| lf line endings everywhere (`.gitattributes`) | the same bytes and diffs on every platform | 2026-09-29 |
| the plugin version lives in `pyproject.toml` and `plugin.json`; a test keeps them equal | json cannot reference toml | 2026-09-29 |
| code and developer sources are json apis, grouped in engine sets (`code`, `codesearch`, `dev`) per language, none a default | a web question must not pay for github; `site:` dorks through web engines get captcha-blocked in bursts | 2026-09-29 |
| the github engines borrow the github cli's login (`gh auth token`), in memory only, sent to api.github.com only | code search needs a login, and a developer who uses the github cli is already signed in; no token in config | 2026-09-29 |
| deep github work (files, history, issues) goes to the `gh` cli, not to weltsuche | the cli reads a repository directly; a search engine only finds it | 2026-09-29 |
| the mcp server sets `alwaysLoad: true`, and the server instructions are short (the skill carries the method) | with tool search, weltsuche's tools were deferred: headless sessions spent one or two extra turns in ToolSearch before the first search. the four tool schemas cost about 1.5k tokens up front; the instructions went from about 600 to 330 | 2026-09-29 |
| tools answer with text content only (`structured_output=False`) | fastmcp gives a tool returning `str` the output schema `{"result": string}` and sends the reply again as structured content, which claude code shows the model: the compact json arrived as a string inside json, every quote escaped | 2026-10-03 |
| `fetch_many(requests)`, a list-shaped fetch, next to `fetch`; a request without its own `max_chars` gets a share of a 48,000-character budget | the first decision was no batch tool: several `fetch` calls in one turn already run at once, a batch would save only each call's envelope. measured usage (2026-10-05, 35 sessions) overturned the premise: 204 fetch calls, every one alone in its turn, none sent together, with the skill saying "in one batch" all along; `search_many`, which takes a list, was used as a list every time. a tool shaped as a list gets batched, an instruction does not. the share is capped at `FETCH_MAX_CHARS`, so a short list costs what the same pages cost one by one. the budget counts text only, and the limit that cut the reply was not `MAX_MCP_OUTPUT_TOKENS` (25,000) but claude code's 50,000 characters, so the tool declares `anthropic/maxResultSizeChars` (`FETCH_MANY_REPLY_MAX_CHARS`, 64,000) | 2026-10-05 |
| a language without a `REGION_BY_LANG` entry is searched without a region | it fell back to `us`: a vietnamese query searched brave as if from the us and gave duckduckgo the invalid region `us-vi` | 2026-10-03 |
| no install scripts: uv's own installer, two `claude plugin` commands and `claude mcp list` | the scripts did little more; the one-time cleanup of a pre-plugin setup no longer applies | 2026-10-03 |
| nothing blocks the event loop: state.json, the cache and the cookie files (with their cross-process locks) are read and written in worker threads (`asyncio.to_thread`), the github cli runs as an asyncio subprocess, log records go through a `QueueListener` thread, and cpu work (extraction, pdf text, focus, the anubis toll) runs in threads too. no aiofiles or filelock: aiofiles is a thread pool as well, and filelock would replace a lock that works with one more dependency | one session's tool calls run on one loop: a lock held by another process (msvcrt retries ten seconds) or a slow disk stalled every concurrent call | 2026-10-03 |
| every import at the top of its module; the engine helpers live in `engines/common.py`, so the registry in `engines/__init__.py` imports every engine module at the top without a cycle | the registry imported the modules inside a function to dodge that cycle | 2026-10-03 |
| korea gets naver and naver blog by default | korean reviews and experience reports live on naver blog, which other engines barely index | 2026-10-03 |
| an engine's answer is judged by its status: challenge, captcha or 403 rests it an hour (`Blocked`), 429 and 5xx as long as the server asks or `BREAKER_RATE_LIMIT_S` (`RateLimited`, `Unavailable`), any other 4xx not at all (`Rejected`, "rephrase"), a network error `BREAKER_NETWORK_S` | every 4xx and 5xx used to park the engine an hour as "blocked": the model took a passing server fault or its own malformed query for a refusal | 2026-10-03 |
| a page incomplete for a passing reason is `partial` and not cached; every fetch reply says whether it came from the cache (`cached`) | a download cut by `TIMEOUT_S`, a failed transcript or readme request, a thin fallback were kept for a day and served again with no sign | 2026-10-03 |
| config.py values are checked against the example's shapes at load | a string where a number belongs failed every search with a TypeError, and `check` crashed on it | 2026-10-03 |
| language tags keep three-letter languages whole and read script subtags (`zh-Hant` is taiwan) | `fil` was searched as finnish, `zh-Hant-TW` from mainland china | 2026-10-03 |
| api engines with a published limit space themselves by it, without jitter (github: 2 s signed in, 6 s anonymous and for code search) | `ENGINE_GAP_S` and the jitter exist to look like a person to web engines; github publishes its limits and counts requests, so a search_many in three languages waited about 12 s for github instead of 4 | 2026-09-30 |
| shop and stay engines take `params`: one vocabulary (price_min, price_max, sort, condition, sold, location, radius_km, checkin, checkout, adults, children, room_type), each engine declaring what it takes in `PARAMS`, checked in `common.check_params`, listed in the search tool's description from the modules | claude should not work out each site's url format, cookies and data layout again; a filter an engine does not take is named, a wrong value refused with the right ones | 2026-10-03 |
| the skill is split: SKILL.md stays loaded once used (and only its first 5,000 tokens survive a compaction), so the operator reference and the sites by topic are files claude reads only when needed | claude code docs, skills: "keep SKILL.md under 500 lines; move detailed reference material to separate files" | 2026-10-03 |
| search operators were checked per engine against each engine's own help page and live through weltsuche: `filetype:pdf` on startpage, duckduckgo and baidu, `site:` on startpage and naver, `intitle:`, github qualifiers, `filename:` in github code search, stack overflow `[tag]`, pubmed field tags, npm `keywords:`, arxiv fields; baidu captchas `site:` | the skill tells claude to dork, so it has to say what works where | 2026-10-03 |
| the skill presents weltsuche as one aid among WebSearch, WebFetch, the github cli and the session's other tools, and tells claude to find where an answer lives, sites with no engine here included (`site:`, or the site's own search page through `fetch`), and to name such a site when it carried the answer | claude should not be glued to one tool, and a site that keeps answering is a candidate engine; the skill went from about 1,900 to 1,060 words | 2026-10-03 |
| the skill and the instructions tell claude to search on its own when a task depends on outside facts, sized to the need | claude should search on its own but stay fast: no search for what memory answers reliably, one search for a fact, the full method for comparisons | 2026-09-29 |

## 3. search engines

| engine | index | how it is asked | what goes wrong, and what weltsuche does |
|---|---|---|---|
| startpage | google | GET the homepage for the form's `sc` code, then POST `/sp/search` with a `preferences` cookie for language and region | a query without results answers with a `notice-noresults-empty` block; a payload with neither that nor a `web-google` block is markup drift. fronted by anubis v1.26.4 (`fast`, difficulty 4 to 5), served only some of the time; a fresh session often gets none. the toll sets `spchal-cookie-verification` (absolute `Expires`, 30 minutes) and `sp_pow` (`Max-Age=300`); the auth cookie `spchal-auth` lived minutes, not the week it once did. `/sp/captcha` means a block (1 h rest). all toll failures of 2026-09 were clock skew (see 6) |
| duckduckgo | bing | `html.duckduckgo.com/html/?q=&kl=` | answers http 202 with an "anomaly" page when it wants a pause: 10 min rest. region codes have their own spellings (`us-en`, `jp-jp`, `tw-tzh`, `xa-ar`) |
| brave | own | `search.brave.com/search`, country as a cookie | 429 quickly once several sessions search; its script bundle carries "no results found" on every page, so only the visible text is checked for it |
| wikipedia | own | the language edition's search api, json | never scanned for refusal phrases; weight 0.5 in the merge; a multi-word query can come back as unrelated articles, which the decoy check drops |
| baidu | own | opens the homepage first for a `BAIDUID` cookie, then `/s?wd=` | class names carry build hashes (`content-right_8Zs40`): match prefixes. aggregate cards have `mu="null"`: skipped. `wappass.baidu.com` or 百度安全验证 is a block |
| yandex | own | `yandex.ru/search/` for ru/uk/be/kk, else `yandex.com` | captchas readily (`showcaptcha`, `showcaptchafast` redirects): 1 h rest. alice widgets share the organic markup: skipped. foreign pages come wrapped in `tr-page.yandex.ru/translate?url=`: unwrapped |
| naver, naver_blog | own | `search.naver.com/search.naver?where=web` and `?ssc=tab.blog.all` | class names are generated per build (`fender-ui_...`, random strings); the click-tracking attribute `data-heatmap-target` (`.link`, `.nblg`) names each result anchor. a result's anchors share its url: breadcrumb (with `›`), title, snippet. every anchor ends with "새 창 열림" (opens in a new window) for screen readers: removed |
| bing | bing | `bing.com/search` | serves well-formed decoy results for another query to clients it distrusts ("framework 16 laptop review" came back as code::blocks downloads); not a default engine; the decoy check drops such pages |
| google | google | not an engine | javascript-only shell for plain clients, captcha on the third request |

code and developer engines, all measured 2026-09-29:

| engine | how it is asked | what to know |
|---|---|---|
| github | `api.github.com/search/repositories` | takes github's qualifiers (`topic:`, `language:`, `stars:>`, `pushed:>`, `in:readme`, `user:`). 30 searches a minute signed in, 10 anonymous; a 403 or 429 with `x-ratelimit-remaining: 0` rests until `x-ratelimit-reset`, not for an hour; 422 is a bad query (no rest); 401 is a revoked login (1 h rest, the token is read again after) |
| github_code | `api.github.com/search/code` with `text-match` | needs a login, 10 a minute; hits link to file pages at a commit sha |
| grepapp | `grep.app/api/search` | exact code, no login; plain curl gets a non-json answer, the browser fingerprint gets json. the snippet is a table of syntax-highlighted lines: read per `<pre>` without separators |
| codeberg | `codeberg.org/api/v1/repos/search`, sort by stars | forgejo api, no login |
| gitlab | `gitlab.com/api/v4/projects?search=`, sort by stars | no login |
| gitee, gitcode | startpage with `site:` (`SITE_ENGINES`) | gitee's own search api answers anonymous clients with nothing; its repository and contents apis work without a login (see 4) |
| hackernews | `hn.algolia.com/api/v1/search?tags=story` | the hit is the discussion, the story's link goes in the snippet |
| qiita | the search page `qiita.com/search?q=&sort=rel`, json in `script[data-component-name=ArticleSearchSearchPage]` | the public api `/api/v2/items?query=` sorts by date and matches bodies: "Python 非同期" gave this week's unrelated articles, even with `stocks:>20` |
| zenn | `zenn.dev/api/search?source=articles` | |
| juejin | POST `api.juejin.cn/search_api/v1/search` | the result list mixes tags, users and courses with articles: only `article_info` entries count |
| csdn | `so.csdn.net/api/v3/search?t=blog` | titles and descriptions carry `<em>` highlights, urls carry tracking parameters |
| v2ex | `sov2ex.com/api/search` | the community's own search engine for v2ex |
| habr | `habr.com/kek/v2/articles/?query=&order=relevance` | ids in `publicationIds`, the rest in `publicationRefs`; `/ru/articles/{id}/` redirects to news and company posts |
| velog | POST `v2.velog.io/graphql`, `searchPosts` | the url slug is korean and needs percent-encoding |
| stackexchange | `api.stackexchange.com/2.3/search/excerpts`, site by `lang` | excerpts mark matches with `<span class="highlight">`: removed before the text is taken. answers link to their question, which `fetch` reads with its answers |
| npm | `registry.npmjs.org/-/v1/search` | |
| crates | `crates.io/api/v1/crates?q=` | crates.io asks api clients for a user-agent that names them; weltsuche sends one instead of chrome's |
| pubmed | `esearch.fcgi` for ids, then `esummary.fcgi` | two requests, the second without the gap |
| arxiv | `export.arxiv.org/api/query`, atom xml | plain words are joined as `all:a AND all:b`, since arxiv ors them otherwise; a malformed query comes back as one entry whose id is `/api/errors` |

shop and stay engines, measured 2026-10-03:

| engine | how it is asked | what goes wrong, and what weltsuche does |
|---|---|---|
| amazon | the store's front page once (cookies), then `/s?k=&low-price=&high-price=&s=` | a cold session gets http 503 "automated access" (amazon.de) or a "continue shopping" interstitial: both are walls (markers). sponsored hits are `AdHolder` and link through a click tracker: every hit is cited as `/dp/ASIN`. a brand can sit in an h2 of its own above the title: the longest h2 is the title. a search without matches leaves the result bar empty and fills the page with unrelated products: no results, not a decoy. `s=price-asc-rank` lists cheap accessories first |
| ebay | `/sch/i.html?_nkw=&_sop=&_udlo=&_udhi=&LH_ItemCondition=&LH_Sold=1&LH_Complete=1` | akamai bot manager (`bm_s` cookies): the first cold search answered, then 403 for every request, the front page included. a blocked engine rests an hour. the first two cards of a page are "shop on ebay" placeholders (`/itm/123456`) |
| kleinanzeigen | `/s-{place}/sortierung:preis/preis:{min}:{max}/{query}/k0l{place id}r{km}` | the place id comes from `s-ort-empfehlungen.json?query=`; class names are generated (tailwind), so an ad is read through `data-adid`, `data-href`, its json-ld and its visible lines; `sortierung:preis` is ascending, `preis-absteigend` does not exist; paid top ads come first |
| airbnb | `/s/{place}/homes?checkin=&checkout=&adults=&price_min=&price_max=&room_types[]=` | the results are json in `script#data-deferred-state-0`; a listing id is base64 `DemandStayListing:<id>`; price_min and price_max are per night, the shown price a total for the dates |

checked 2026-10-03 and not added: pypi's search (a javascript challenge),
mojeek (a javascript challenge), dev.to's search endpoint (empty answers),
bilibili's search api (answered once, then 412), reddit search through arctic
shift (needs a subreddit or an author) and pullpush (a thin index that does
not rank by relevance), lobste.rs (anubis, and small).

highlights: every engine's search highlights (`<em>`, `<b>`, `<mark>`,
`<strong>`) are removed before the text is taken (`engines.plain`, which
`text()` uses too). a space at every tag boundary keeps words in neighbouring
elements apart, but around a highlight it split chinese words and russian
compounds ("爬虫 的进阶", "Python -приложение"; csdn, habr, startpage,
baidu). parsing a results page still takes 8 to 45 ms.

`site:` dorks: a burst of eight `site:` probes (gitee, gitverse, ...) got
startpage to answer with `/sp/captcha-block` and duckduckgo with its 202
anomaly page, both resting for an hour; baidu captchas `site:` queries even
alone. site engines stay few and spaced as their carrier.

merging: reciprocal-rank fusion with a bonus for agreement between engines.
urls are normalized first: `www.`, `m.`, `mobile.` dropped anywhere left of the
registrable name (`post.m.smzdm.com` = `post.smzdm.com`), tracking parameters
(`utm_*`, `fbclid`, `srsltid`, ...) dropped. a page where no hit mentions a
query token (latin words of 3+ letters, numbers of 3+ digits, cjk bigrams) is
a decoy and is neither shown nor cached.

## 4. walled sites and their routes

| site | a plain client gets | routes that work (2026-09-29) | tried and dropped |
|---|---|---|---|
| reddit | www: "prove your humanity" page, or a form its own script submits (solution = a token doubled); `.json`: 403; `old.reddit.com`: redirect to `/login?reason=lor2`; rss: 403 "blocked by network security" | redlib (2 of 7: redlib.catsarch.com, safereddit.com), whole thread with comments; arctic shift api (`/api/posts/ids`, `/api/comments/tree`), may lag new posts; pullpush api (100 comments) | answering the javascript check (not done on principle). claude code's WebSearch and WebFetch are refused by reddit entirely |
| x | a javascript app | fxtwitter api (`api.fxtwitter.com/{user}/status/{id}`, `/status/{id}` for `x.com/i/status/...`, `/{user}` for a profile): text, counts, community note, quote, no replies; nitter (1 of 7, nitter.tiekoetter.com, later behind anubis) for timelines | `api.fixupx.com` (no such host: fixupx.com only serves embeds), vxtwitter api (cloudflare 403), xcancel (http 451), nitter instances behind anubis `preact` |
| youtube | the only text is the footer | youtube's player api `youtubei/v1/player` with the android client (`20.10.38`): title, channel, views, length, description, caption tracks; a track's `baseUrl` minus `&fmt=srv3` returns timed text (escaped twice); invidious html (2 of 7) | web client caption urls (empty: they need a proof-of-origin token), `WEB_EMBEDDED_PLAYER` and `TVHTML5_SIMPLY_EMBEDDED_PLAYER` clients (playability error), invidious captions api (empty) and comments api (500), piped (needs javascript). summaries: there is no public api; claude summarizes the transcript |
| instagram | a javascript app | kittygram (3 of 8; profile text, post captions), instagram's own `/p/{code}/embed/captioned/` | proxigram (0 of 5) |
| tiktok | a captcha wall | tiktok's oembed (`tiktok.com/oembed?url=`): a video's caption and author | `embed/v2` (500), proxitok (0 of 14); profiles have no route |
| threads | a javascript app (`og:description` holds the post) | `{post url}/embed`: the post and its counts | |
| bluesky | a javascript app (`og:description` holds the post) | `public.api.bsky.app/xrpc/app.bsky.feed.getPostThread`: the post and its replies | |
| stack exchange | 403 | the stack exchange api: question and 30 answers by votes; 300 requests a day per address without a key; honours `backoff` | anonymousoverflow (1 of 34 alive, 25 s per page) |
| medium | free posts read directly | libmedium (1 of 5) only when the direct read is thin or fails | scribe (mostly dead or behind bot checks) |
| telegram | `t.me/s/{channel}` reads directly; a single post's page holds only its preview | a post (`t.me/{channel}/{id}`) through its embed page `?embed=1`: text, date, views | |
| naver blog | the post sits in a frame the page builds by javascript (no readable text) | the mobile page `m.blog.naver.com/{id}/{post}` carries the post; `PostView.naver?blogId=&logNo=` maps to it too | |
| crates.io | a javascript app (73 characters of text) | `crates.io/api/v1/crates/{name}?include=default_version` for the facts; `/{version}/readme`, asked for json, names where the rendered readme lives | the full crate answer lists every version: 440 kb for serde |
| pubmed | a cookie check, http 203 "cookies must be enabled", whose script sets a cookie and reloads | ncbi's `efetch.fcgi?db=pubmed&rettype=abstract&retmode=text`: citation, title, authors, abstract | |
| quora | captcha | none | quetre (0 of 22) |
| imdb | aws waf "verify that you're not a robot" | none | libremdb (0 of 23) |
| bilibili | page metadata is a javascript template | none | api (412, "risk control") |
| zhihu, tieba | 403 | none | zhihu api (403, needs a signed header) |
| reuters, fandom, genius, goodreads, tumblr | read directly | (no route needed) | neuters, breezewiki, dumb, biblioreads: not needed |
| github repository front pages | the readme amid navigation, no license or push date; a headless session paged through raw api json in eight slices instead | github's api: facts and readme in one reply (`sites.github_repo`); a reserved path of the same shape (`/topics/x`) gets a 404 there and is read as a page | |
| github file pages (`/blob/`) | the page chrome and the line numbers, no code | the raw file at `raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}`; codeberg `/src/` -> `/raw/`, gitlab `/-/blob/` -> `/-/raw/` | |
| gitee | http 405 behind its "nox" bot wall on every page; `raw.giteeusercontent.com` timed out | `gitee.com/api/v5/repos/{o}/{r}` (facts), `/readme` and `/contents/{path}?ref=` (base64), no login | |

frontend rules learnt the hard way:

- some instances answer every path with their status or home page
  (`q.opnxng.com`, `d.opnxng.com`, `proxitok.r4fo.com`), so an answer only
  counts when it mentions the url's own words.
- those words must be clean: letters only, 4+, one case. ids are not words.
  treating `1umaxso`, a tweet id or an instagram shortcode (`DW90CiWiSim`) as
  words judged good instances "unrelated" and parked nitter and kittygram for
  six hours (found twice on 2026-09-29).
- a 404 speaks about the post (deleted), not the instance: never parked for it.
- reddit ignores the slug: `/comments/{id}/anything/` resolves to the post.

## 5. reading pages

- **url vetting.** http(s) only; every resolved address must be global
  (no loopback, private, link-local, cgnat, multicast; ipv4-mapped ipv6
  unwrapped); every redirect hop vetted before its request, and curl pinned
  to exactly the vetted addresses (`CURLOPT_RESOLVE`), so dns rebinding
  cannot move the connection; the address curl connected to is checked again
  before the body is read. a keep-alive connection reused later was vetted
  when it opened. curl_cffi types `curl_options` as `dict[CurlOpt, str]`, but
  list options like `RESOLVE` take a list (pinned to 1.1.1.1 on 2026-09-29,
  curl connected to 1.1.1.1).
- **bot walls at http 200.** only a short page can be one (under 2,000
  characters of visible text); strong phrases count in its source or text,
  weak ones ("captcha", "access denied") only in the text of a page under 600
  characters, never in its source (a comment form's recaptcha script is not a
  wall). observed walls: reddit ("prove your humanity"), anubis ("making sure
  you're not a bot"), imdb/aws waf ("verify that you're not a robot", with a
  curly apostrophe), frontend instances ("checking you are not a bot", "this
  verification process may take a while"). a 200 page is judged by its text
  alone: the url check for a challenge redirect (`/showcaptcha`, `/sorry/`,
  `wappass.baidu.com`) runs for engines and for a fetched page with an error
  status. yandex's captcha page stays a wall by "smartcaptcha" in its
  source; the word "showcaptcha" does not occur in it (a saved page, checked
  2026-10-05). "showcaptcha" must not come back as a text marker: every
  mediawiki page's config names `wgConfirmEditForceShowCaptcha`, and a short
  one (a stub, a redirect page) read as a wall for it.
- **anubis.** two protocol generations: v1.15 (bugs.winehq.org) loads a
  script that POSTs `api/make-challenge` for `{challenge, rules}` and passes
  without an id; v1.26 (startpage) embeds `{challenge: {id, randomData, ...},
  rules}` in the page and wants the id back. the toll is `sha256(data +
  nonce)` with `difficulty` leading hex zeros; python manages about 0.35
  million hashes a second, so difficulty 6 costs about 50 s and 7 is refused,
  as is the `preact` algorithm. `anubis_base_prefix` comes from the page and
  could redirect the toll (`"@192.168.1.1/x"`): only a plain path is accepted,
  the toll urls are vetted and never follow redirects.
- **charset.** byte order mark, then the http header, then `<meta>` in the
  first 4 kb, then strict utf-8, then charset_normalizer on a sample.
  labels map to the whatwg supersets (`shift_jis` to cp932, `gb2312`/`gbk` to
  gb18030, `latin1` to cp1252, `euc-kr` to cp949, `big5` to big5hkscs).
  python accepts `hex` and `base64` as codec names but they are
  bytes-to-bytes: rejected. kakaku.com declares shift_jis only in `<meta>`.
- **extraction.** trafilatura's `bare_extraction` once, with metadata; its
  exhaustive date search took 9 of 11 seconds on rfc 9110 and is off. the
  article extractor kept 53 to 99 percent of normal pages' visible text but 1
  percent of rfc 9110 and 3 percent of a bugzilla page, so under 20 percent of
  more than 1,000 characters the full visible text is returned instead, with a
  note. `<pre>` blocks keep their layout (private-use placeholders survive the
  whitespace folding). selectolax: `strip_tags(recursive=True)`, not
  `decompose()` on nested matches.
- **thin pages.** under 500 characters from more than 20 kb of html is a
  javascript page: a note, and the meta description (smzdm's carried the whole
  argument). embeds are short by design and exempt.
- **downloads.** curl_cffi's streaming `aclose()` waits for the whole
  transfer: after a cut, `resp.quit_now.set()` makes curl abort at its next
  chunk; the read has an overall `TIMEOUT_S`; content types that are not text
  (audio, video, images, archives) are refused from the headers before any
  byte is read (a radio stream never ends).
- **pdf.** a cut pdf cannot be parsed (refused past `FETCH_MAX_BYTES`, 32 mb);
  "encrypted" pdfs mostly open with an empty password; a scan without a text
  layer is an error, not an empty success; unreadable pages are counted in a
  note.

## 6. environment and platform gotchas

- **clock skew.** a local clock that runs ahead (a stopped time service)
  breaks startpage. curl drops a cookie whose
  absolute `Expires` is already past by the local clock; chrome corrects the
  expiry by the response's `Date` header. without the same correction
  (`net.restore_skewed_cookies`) every startpage toll failed with "cookies
  appear to be disabled" (http 500), and each failure parked startpage for an
  hour. `python -c` with a `Date` header comparison shows the skew.
- **host-only cookies.** curl names a domain cookie with a leading dot and a
  host-only one without, and curl_cffi hands a cookie's `domain_specified` to
  curl as "include subdomains". the cookie file stores the domain with or
  without the dot; reading it back with `domain_specified` set for every
  cookie turned startpage's host-only `sp_return` into a domain cookie that
  startpage's host-only deletion could no longer remove: every startpage
  request then redirected in a loop until it expired (2026-10-03).
- **several sessions, several servers.** every claude code session runs its
  own weltsuche process (several at once are common; the old launcher ran
  three python processes each). they share one ip, one state file, one cookie jar
  per engine:
  - on windows `os.replace` onto a file another process has open raises
    PermissionError (28 percent of replaces with 2 writers and 3 readers in a
    test): writes retry, and every read-modify-write holds a lock file
    (`msvcrt.locking`, `fcntl.flock` on linux).
  - a state file that cannot be read must never be written back empty: that
    erased every open breaker for every process.
  - spacing slots are reserved (claimed and dated in one locked step);
    reading the last time and writing the new one separately let processes
    fire at the same moment.
  - cookie saves merge only what the request changed; whole-jar writes let
    the last writer erase a toll another session had paid.
- **editing a running server's code.** every module is imported at start, so
  a running server keeps the code it started with; `/reload-plugins` or a new
  session runs an edit. to test an edit from inside a running session,
  start a headless one: `claude -p "call the tool once and say what came
  back" --allowedTools mcp__plugin_weltsuche_weltsuche__fetch_many` runs a
  new server from the checkout and ends. a claim about a client limit needs
  a control in the same kind of session (2026-10-05: 8 pages from
  `fetch_many` arrived inline, a 51.5 kb reply from `fetch`, which declares
  no limit, was saved to a file).
- **claude code and mcp.**
  - claude code keeps only the startup lines of an mcp server's stderr: a
    failure mid-session leaves nothing, hence `weltsuche.log` in the data
    folder.
  - fastmcp turns a tool's exception into a bare `str(e)` and logs nothing:
    every tool has its own boundary that names the failure and logs the
    traceback.
  - tool calls run concurrently (the sdk starts a task per request):
    measured 8 fetches in 13 s against 42 s one by one.
  - `MAX_MCP_OUTPUT_TOKENS` defaults to 25,000; fetch pages are 12,000
    characters.
  - the limit a text reply meets first is its length: past 50,000
    characters claude code saves it to a file and shows the model a 2 kb
    preview, whatever its token count (48,000 characters of chinese arrived
    inline, 51,179 of english did not; measured 2026-10-05). a tool lifts
    that for itself with `_meta["anthropic/maxResultSizeChars"]` in its
    tools/list entry, up to 500,000, and the token limit then no longer
    applies to its text (claude code's docs, mcp, "raise the limit for a
    specific tool"). the key is claude code's own; what another mcp client
    does with a long reply was not checked.
  - `claude mcp get NAME` and `claude mcp list` start the servers to check
    their health; the readme's install steps use `claude mcp list` to build
    the plugin's environment once. `alwaysLoad` makes session start wait at
    most 5 seconds for a server, less than a first environment build.
  - a plugin from a local-directory marketplace runs its mcp server from the
    checkout (`${CLAUDE_PLUGIN_ROOT}` is the checkout's path, `claude mcp list`
    shows it); its data folder is
    `~/.claude/plugins/data/{plugin}-{marketplace}` and is deleted on
    uninstall unless `--keep-data`.
  - claude code's docs (plugins/loading, "in-place and copied plugins"):
    a relative-path plugin in a marketplace added from a local directory
    loads in place, skill included, and edits take effect at the next
    session start or `/reload-plugins` without a version bump. the install
    still writes a copy of the working tree (`.venv/` and `data/` included,
    about 125 mb) to `~/.claude/plugins/cache/weltsuche/weltsuche/{version}`
    and records it as `installPath`; nothing loads from it. an update marks
    the old copy `.orphaned_at` and removes it 14 days later.
- **uv.** a fresh environment takes about 16 s with a cold cache; hardlinks
  from uv's cache to a checkout on another drive fail and uv copies instead (10 s);
  the plugin's data folder sits in the home directory, normally on the
  same drive as uv's cache, and links. editors and claude code's
  language server need `[tool.pyright] venvPath/venv` to see uv's `.venv`.
- **git on windows.** `core.autocrlf` would give checkouts crlf; the repo
  pins lf.

## 7. operating it

- what is going on: `uv run python -m weltsuche status` (engines resting,
  cache, data folder), `... check`, `... fetch URL` (notes, `tried`, error),
  and `weltsuche.log` in the data folder (every request, every traceback).
- refreshing a fixture: capture the page with the engine's own request, save
  `resp.text` to `tests/fixtures/{engine}.html`, then scrub it before
  committing: cookie values (`BAIDUID`, `yandexuid`), token and session
  values (`token":"..."` in any case, naver's `g_suid`/`g_puid`), startpage's
  form `sc` code, the client ip (`X-Real-Ip` in anubis challenges, baidu's
  `connIp` and `oriConnIp`, also as a plain integer), and the visitor's
  location, which result pages carry from geo-ip: coordinates (startpage
  `req_loc`, brave `geoLocation`, yandex weather links), city and region
  (startpage `gcs`/`gr`, brave `city`/`postal_code`/`user_location_label`,
  yandex `lr=` and `regionData`), timezone. replace values with neutral
  placeholders or zeros so the markup stays intact, then search the file
  for the place names again.
- adding an engine: a module with `NAME`, `HOME`, optional `WEIGHT`,
  `BREAKER` and `PARAMS`, and `search()` that requests with `check=False` and returns
  `settle(resp, parse, query)`, or reads a json api through `api_json`; a
  fixture and a parser test; a place in `ENGINES_BY_LANG` or `ENGINE_SETS`,
  and the module in `engines.MODULES`. `check` rejects names that exist
  nowhere.
- the checkout and the plugin keep separate data folders (`data/` and
  `${CLAUDE_PLUGIN_DATA}`), so a breaker or cache in one is not seen by the
  other; the ip reputation behind them is shared all the same.
- adding a site route: a reader in `sites.py` that returns a `Page` and
  raises `ApiError` for a wrong answer, an entry in `SITES` in order of
  preference, a test for its url matching.

## 8. open issues and ideas

- found in the review of 2026-10-03 and left open: a frontend instance is
  parked six hours for any failure, a local timeout included; the decoy check
  takes an engine that corrected a typo ("pytohn") for a decoy; a
  meta-refresh or javascript redirect page is read as the page; the
  extraction thread (`_document`) has no time bound; `engines_status` lists
  engines only, not the rests of fetch hosts and frontend instances.
- found in the review of 2026-10-05 and left open, `fetch_many`: the reply
  waits for its slowest page (three run at a time) and has no time bound of
  its own; the same url twice in one call (two slices of one long page) is
  read twice, both miss the cache before either has stored the page;
  nothing limits the length of the list, and more than about 40 pages, or
  `max_chars` values that add up past 64,000 characters, make a reply that
  claude code saves to a file again; every page gets at least 200
  characters, so past 240 urls the text alone exceeds the budget; the budget
  counts characters, not tokens, and what a full reply of chinese, japanese
  or korean text costs in tokens was not measured; `fetch`'s docstring says
  it never raises, but its cache read and write and the focus search run
  outside its try, and only the tool boundary (`_answer`) and `fetch_many`
  catch what it lets through.
- found in the same review and left open, reading pages: the weak markers
  skip script and style contents, so a small results page with no hits
  whose only "captcha" sits in a script is no longer a block; the engine
  would answer "parser found nothing" and not rest (no such page has been
  seen). `<noframes>` counts as visible text: market.yandex.ru keeps its
  page state as json in `<noframes data-apiary="patch">`, the visible text
  was 221,778 characters of it, the article of 2,713 fell under the 20
  percent rule, and the json came back as "the page's full visible text";
  dropping the tag everywhere is no fix, in a frameset document it holds the
  only readable text. `author` and `date` are the extractor's guesses on a
  page without a byline: wikipedia's world war ii article came back with the
  author "Authority control databases" and the date 2001-11-10, a docs page
  with a navigation heading as its author. kinopoisk.ru film pages redirect
  to `sso.passport.yandex.ru/push`, a javascript page, and read as "no
  readable text"; no route exists.

- rate limits: brave, duckduckgo and yandex parked themselves quickly while
  five sessions searched. most of that traffic came from the old code, which
  did not reserve slots across processes, so no gap was tuned blind (decided
  2026-09-29). count the 429s per engine in `weltsuche.log` once only plugin
  processes run; if brave still rests often, a per-engine gap is the fix.
- a difficulty-6 anubis toll holds the gil for about 50 s in a worker thread.
- youtube may retire the android client version in `YOUTUBE_CLIENT`; the
  player api then answers with a playability error, and invidious carries.
- nitter hangs on one instance; fxtwitter is the primary for posts.
- qiita reads its search page's react payload; a redesign of that page is
  markup drift (`ParseError`), and the date-sorted api is the fallback to
  consider then.
- the gitee and gitcode site engines depend on startpage being awake; when it
  rests they rest too. baidu would carry them but captchas `site:`.
- yandex answered with its captcha and rested an hour during the check of
  2026-10-03; bing answered with no results (it is no default). duckduckgo
  once answered a page its parser could not read, then worked again; the
  page was not kept, so the cause is open.
- the readme of a crate reads "ser ializing": `visible_text` puts a space at
  every tag boundary, also inside a word split by inline markup.
- ideas from other projects (2026-10-03): engine-neutral freshness and
  include/exclude-domain filters mapped to each engine's operators
  (free-search-mcp); openalex, crossref and semantic scholar in `science`
  (paper-search-mcp); an opt-in headless browser for "no readable text"
  pages whose error names the install command (free-search-mcp,
  open-webSearch).
- ebay: akamai answers 403 once it distrusts a client; a route around it
  would need its sensor script, which a plain client cannot run.
- ebay's "no results" words are known in english and german only: an empty
  search on ebay.fr, .it, .es, .nl or .pl reads as markup drift. its "fewer
  words" cards after a search without exact matches may count as hits. both
  unverified, no such page saved yet.
- a streamed page load (`fetch`) saves its cookies once the body is in: a
  read of the cookie file by another request of the session in between can
  bring back a cookie the response just deleted. plain requests save right
  away, under the session's cookie lock.
- the amazon product and airbnb room routes go through their engine's
  session, so while the engine rests after a block, those pages are not
  read either: the block is respected, not walked around through `fetch`.
- ideas: a reddit search engine through redlib's search; transcripts in
  another language through youtube's `tlang`; gitflic.ru (a russian code
  host), not verified yet.

## 9. timeline

- 2026-09-27: first version, a python project with a self-bootstrapping
  launcher, registered by hand as a user-scope mcp server.
- 2026-09-29: audit against claude code's WebSearch; url vetting, walls,
  charsets, extraction, per-host spacing, startpage (clock skew); public
  frontends, apis and route racing for walled sites; two independent reviews
  (24 findings, fixed); pydantic models; the plugin with uv; this repo.
  later that day: code and developer engines (github, github code search,
  grep.app, codeberg, gitlab, gitee and gitcode through startpage, hacker
  news, qiita, zenn, juejin, csdn, v2ex, habr, velog), engine sets, the
  github cli login, raw-file and gitee fetch routes, and tool routing in the
  skill. then: headless autonomy runs (`claude -p` with questions that never
  say "search"): both sessions invoked the skill on their own and used
  native chinese queries and the `code` set; fixes from what they showed:
  `alwaysLoad`, shorter instructions, a github repository route, no gap
  between the two api calls of one page read.
- 2026-10-03: review against claude code's plugin docs and a live check of
  every engine and route. fixed: the host-only cookie bug behind startpage's
  redirect loop, the root `.mcp.json` doubling as a broken project server,
  double-encoded tool replies, the `us` region fallback. added: naver and
  naver blog, stack overflow search, npm, crates.io, pubmed, arxiv, the
  `packages` and `science` sets, `fetch(focus=...)`, routes for telegram
  posts, naver blog posts, crates.io and pubmed. removed: the install
  scripts. the test fixtures were scrubbed of the visitor location result
  pages carry. then two independent reviews (error handling and retry
  semantics, correctness): an ipv6 url poisoned every later fetch of the
  process (curl RESOLVE), every 4xx and 5xx rested an engine an hour,
  degraded pages were cached, focus took 15 s on one long line and saw no
  word in hindi, language tags were cut to two letters, config values of
  the wrong type failed far from their cause; all fixed, each with a test
  where the cause was subtle.
  later that day: the shop and stay engines (amazon, ebay, kleinanzeigen,
  airbnb) with `params`, product and room routes, the skill split in three
  files, and no blocking file work on the event loop. a review of that work
  found the airbnb room route requesting look-alike hosts off the public
  web, amazon links without `www.` crashing, kleinanzeigen ads borrowing
  their neighbours' facts, prices of a million sent as `1e+06`, and a
  cookie file read that undid a newer deletion; all fixed, with tests.
- 2026-10-05: the session transcripts of 35 real uses were read for how the
  tools get called. `search_many` was used as designed (2 to 8 queries a
  call, en, de, zh, ja, ru, he, ko); `fetch` never was: 204 calls, one per
  turn, each waiting for its reply, though the skill asked for one batch
  since the start. added `fetch_many`. startpage's "parser found nothing"
  showed up repeatedly in those sessions but not in two live checks, so it
  is filed as intermittent, not fixed. testing `fetch_many` live found
  "showcaptcha" as a text marker blocking short mediawiki pages, whose
  config names `wgConfirmEditForceShowCaptcha`; it is a url check now, and
  the weak markers skip script and style contents. a review the same day
  found two faults in `fetch_many`: one or two urls got 48,000 or 24,000
  characters each, more than `fetch` gives, and a reply of 8 long pages
  (51,179 characters) was saved to a file by claude code, so no page
  arrived. the share is capped at `FETCH_MAX_CHARS` and the tool declares
  its own reply limit. what the review left open is in section 8.
