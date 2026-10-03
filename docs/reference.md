# weltsuche reference

everything the [readme](../README.md) leaves out: the skill's files, where
things live, the tools and their failures, how pages are read, the engines
and their filters, how weltsuche stays welcome, every setting, tools that
complement it, and the layout of the repository. why it is built this way:
[knowledge.md](knowledge.md).

## the skill

the skill is three files, so claude pays only for what a task needs:

| file | loaded |
|---|---|
| `skills/weltsuche/SKILL.md` | when the skill is used (about 1,700 tokens): what the tools do, depth, where to look, the method, pdfs, errors and retries |
| `skills/weltsuche/operators.md` | when claude needs search operators: what each engine understands (checked against their own help pages and live), examples by purpose |
| `skills/weltsuche/sources.md` | when the engines are not enough: where answers live by topic, sites with no engine here |

## where things live

| what | where |
|---|---|
| the plugin: mcp server `weltsuche`, skill `weltsuche:weltsuche` | loaded in place from this checkout (a local-directory marketplace) |
| python environment, config.py, state, cache, cookies, log | `${CLAUDE_PLUGIN_DATA}` (`~/.claude/plugins/data/...`), kept across plugin updates |
| python itself, if the machine has none | uv's managed pythons |

`uv run python -m weltsuche status` in this folder prints the data folder of a
checkout; the plugin's own is `~/.claude/plugins/data/weltsuche-weltsuche`
(`${CLAUDE_PLUGIN_DATA}`): `uv run python -m weltsuche --data
~/.claude/plugins/data/weltsuche-weltsuche status` shows its state.

## the tools

tool calls run concurrently: the model can fetch several pages at once, and
each answer is a model from `weltsuche/models.py`, sent without fields that
still hold their default.

| tool | what it does |
|---|---|
| `search(query, lang, region, engines, max_results)` | one query, several engines in parallel, merged and ranked, best first. a hit several engines agree on ranks higher. `lang` may be a tag like `zh-TW`, which also sets the region; `lang` and `max_results` default to `DEFAULT_LANG` and `MAX_RESULTS`. `engines` says per engine who answered, from the cache or not, and why the others did not; `summary` names them in one line. |
| `search_many(queries)` | several searches at once (three at a time), typically one question in several languages. |
| `fetch(url, lang, max_chars, offset, raw, focus)` | one slice of a page's text with title, author, date and the page's own description; pdfs too; `next_offset` continues a long page. `focus` (a few words) returns only the passages that mention the most of them, in page order, each marked `[at N]` with its offset. `url` is the address to cite, `via` what was actually read, `notes` what the text is and lacks, `tried` the routes that failed first, `error` why nothing could be read, `partial` that the page is incomplete for a reason that may pass, `cached` that it came from the cache. see "reading pages". |
| `fetch_many(requests)` | several pages in one call, three at a time: the way to read urls already in hand (search hits, links on a page). each entry is a full fetch call. a request without its own `max_chars` gets a share of a fixed text budget (`FETCH_MANY_BUDGET_CHARS`, 48,000 characters), never more than `FETCH_MAX_CHARS`, so asking for more urls at once returns less of each; `focus` on each cuts that further. the tool declares `anthropic/maxResultSizeChars` (`FETCH_MANY_REPLY_MAX_CHARS`, 64,000): claude code saves an undeclared tool's reply to a file once it is longer than 50,000 characters, and the text budget plus each page's envelope passed that from about six urls on. one url's failure never drops the others' pages. |
| `engines_status()` | engines known, which are resting and why, cache size. |

an exception inside weltsuche comes back as an answer that names the tool and
the failure, and its traceback goes to `weltsuche.log` in the data folder; it
never ends as a bare "error executing tool". replies are plain text content,
not mcp structured content, so claude reads the compact json once, unescaped.

several `fetch` calls sent in one turn run at the same time too, and the
first design relied on that instead of a batch tool. measured usage showed
the model never sends them together: across 35 sessions, 204 fetch calls came
one per turn, each waiting for its reply, while `search_many`, which takes a
list, was used as a list every time. a tool shaped as a list gets batched; an
instruction to call a single-url tool several times does not. hence
`fetch_many`. what costs tokens is still the text, which `focus` cuts.

every failure tells the model whether a retry can help:

| failure | the model sees | retry |
|---|---|---|
| engine rate limit (429, github, stack exchange quota) | `rate limited, resting Ns, retry after that` | after N seconds |
| engine server error (5xx) | `server error, resting Ns, retry after that` | after N seconds (`Retry-After`, else `BREAKER_RATE_LIMIT_S`) |
| engine network error (timeout, dns, reset) | `network error, resting Ns, retry after that` | after `BREAKER_NETWORK_S` |
| captcha, challenge, 403 | `blocked, resting 3600s` | after an hour, other engines meanwhile |
| a 4xx the query caused, an api's error message | `the engine rejected the query, rephrase or use another engine` | no; rephrase |
| github code search without a login | `needs a github login: ... gh auth login` | after the login, picked up within 5 minutes |
| markup drift, a weltsuche bug | `parser found nothing usable ...`, `internal error, a weltsuche bug` | no |
| page: network error, 429, 5xx | `network error, worth a retry later`, `http 503: ... retry in Ns` | once, later |
| page: incomplete (download out of time, failed transcript or readme, thin fallback) | `partial: true`, not cached | later, when the missing part matters |
| page: refused url, 404, not text, unreadable pdf | `refused: ...`, `http 404`, ... | no |

## reading pages

`fetch` loads a page the way a browser would and returns text, or an `error`
that says why not. errors are never cached, so a later try goes out again.

| case | what `fetch` does |
|---|---|
| `file://`, another scheme, localhost, a private or link-local address, a redirect to one | refused before the request; curl is then pinned to the addresses that passed, so a dns answer that changes in between (dns rebinding) cannot redirect the connection, and the address it connected to is checked again before any body is read. a fetched page can ask the model to fetch something; this keeps that on the public web |
| anubis proof-of-work gate | paid, as a browser pays it, then the page is loaded. the toll requests are vetted like any page and never follow a redirect |
| any other bot wall (a short page with a captcha, "prove your humanity" or "cookies must be enabled") | `error: blocked: the page is a bot wall` |
| a page with no readable text (a javascript app shell or bot check) | `error: no readable text` |
| charset declared only in `<meta>` (kakaku.com, shift_jis) | decoded by that declaration, as a browser does |
| the article extractor keeps under a fifth of the visible text | the full visible text, with a note saying so (rfc 9110: 442,397 characters instead of 5,220). code blocks keep their indentation |
| very little text in a big html page | the text, a note that the page is built by javascript, and `description`, which often carries the gist |
| a pdf | its text, the first 300 pages, a note for unreadable pages; a scan without a text layer, a password-protected pdf and one larger than `FETCH_MAX_BYTES` are errors |
| an image, audio, video or archive | `error: not a text document`, known from the headers, so nothing is downloaded (a radio stream included) |
| a body past `FETCH_MAX_BYTES`, or one still downloading after `TIMEOUT_S` | the transfer is stopped there and the text is marked as cut |
| a site that walls off plain clients | read through its routes, below |

### walled sites and their routes

a plain client reads nothing from reddit (javascript bot check), x, instagram,
threads, bluesky and crates.io (javascript apps), youtube (a page whose only
text is its footer), tiktok (captcha), stack exchange (403), naver blog (the
post sits in a frame) or pubmed (a cookie check). each has routes, tried in
order: the site's own public api or embed where one exists, public frontends,
archives. the next route starts when one fails or takes longer than six
seconds (two at most at once); the first good answer wins. every route that
failed first is listed in `tried`.

| site | routes, in order | measured 2026-09-29 |
|---|---|---|
| reddit | redlib frontends, arctic shift archive, pullpush archive | redlib 2 of 7 instances; the archives give the post and its comment tree |
| x / twitter (also fixupx.com, fxtwitter.com links) | posts: fxtwitter api, then nitter; profiles: nitter, then fxtwitter's profile | fxtwitter: text, counts, community note, no replies; nitter 1 of 7 |
| youtube | youtube's player api, then invidious | title, channel, views, description and the transcript (manual track in `lang`, else the auto-generated one); invidious 2 of 7 |
| instagram | kittygram frontends, instagram's embed page | a post's caption and likes |
| tiktok | tiktok's oembed | a video's caption; profiles have no route (proxitok 0 of 14) |
| threads | threads' embed page | a post and its counts |
| bluesky | bluesky's public api | a post and its replies |
| telegram (`t.me/channel/123`) | the post's embed page | a channel post's text, date and views (measured 2026-10-03) |
| naver blog (`blog.naver.com/id/123`, `PostView.naver`) | the mobile page, `m.blog.naver.com` | the post itself (measured 2026-10-03) |
| crates.io (`crates.io/crates/name`) | crates.io's api | description, version, downloads, repository and the readme (measured 2026-10-03) |
| pubmed (`pubmed.ncbi.nlm.nih.gov/123/`) | ncbi's e-utilities | citation, title, authors and abstract (measured 2026-10-03) |
| medium | the page itself, then libmedium | libmedium only when medium's own page is thin or fails |
| stack exchange (stack overflow and its ru/ja/pt/es editions, superuser, serverfault, askubuntu, *.stackexchange.com) | the stack exchange api | a question and up to 30 answers, best first; 300 requests a day without a key |
| a file on github, codeberg or gitlab (`/blob/`, `/src/`, `/-/blob/` urls) | the raw file | github's file page shows line numbers only, gitlab's is javascript |
| a github repository (`github.com/owner/repo`) | github's api (with the github cli's login when there is one), then the page itself | description, stars, forks, language, license, topics, last push, open issues, and the readme |
| gitee | gitee's public api | a repository's facts and readme, or one file; every gitee page is a bot wall (http 405) |
| amazon product (`/dp/ASIN`, `/gp/product/ASIN`, any store) | the amazon engine's session | price, rating, availability, bullet points, description, details; a cold session gets an interstitial instead (measured 2026-10-03) |
| airbnb room (`/rooms/ID` on any airbnb domain) | the room's page on airbnb.com: its own data and json-ld | kind, capacity, ratings, superhost, description; the price for dates is in the search results (measured 2026-10-03) |

frontend instances come from libredirect's list
(`github.com/libredirect/instances`, refreshed daily, cached a day), after the
ones in `FRONTENDS_PREFERRED` (`redlib.catsarch.com` for reddit). an instance
that fails is skipped for six hours by every process; a 404 does not count,
since it speaks about the post, not the instance. a frontend's answer only
counts when it mentions the url's own words, because some instances serve
their status page on every path. quora (quetre 0 of 22) and imdb (libremdb
0 of 23) have no route and come back as `blocked` or with their http error.

## engines

| engine | index | default for | notes |
|---|---|---|---|
| startpage | google | every language | pays startpage's anubis proof-of-work toll when asked (sha-256, difficulty 4 to 5, one to several seconds); the auth cookie lives minutes, so the toll comes round often. posts the same form a browser posts, with language and region |
| duckduckgo | bing | every language except ko | html endpoint, honours a region code |
| brave | own | every language except zh, ru and ko | country as a cookie; rate-limits quickly when several sessions search |
| wikipedia | own | every language | the language edition's search api; its hits count half in the merge |
| baidu | own | zh | opens the homepage first for its cookie |
| yandex | own | ru | captchas sometimes; rests an hour when it does |
| naver | own | ko | korea's own search engine; read through the click-tracking attribute `data-heatmap-target`, since its class names change with every build |
| naver_blog | own | ko | naver's blog tab: korean reviews, buying advice and experience reports |
| bing | bing | nobody | serves decoy results to clients it distrusts; the decoy check drops them, ask for it explicitly if you want to test it |

google itself is not an engine: it serves a javascript-only shell to plain
clients and answered the third request with its captcha page.
startpage is the google index without that.

### code, package, developer and paper engines

none of these is a default: name them in `engines`, or name a set. a set
(`ENGINE_SETS`) stands for its engines in the search language, so
`engines: ["code"]` with `lang: "zh"` asks github and gitee. these indexes
match keywords, not sentences (github returns nothing unless every word
appears): 2 to 4 words work best.

| set | `*` | zh | ja | ko | ru | pt, es |
|---|---|---|---|---|---|---|
| `code` | github, codeberg, gitlab | github, gitee | | | | |
| `codesearch` | github_code, grepapp | | | | | |
| `dev` | hackernews, stackexchange | juejin, csdn, v2ex | qiita, zenn, stackexchange | velog | habr, stackexchange | stackexchange |
| `packages` | npm, crates | | | | | |
| `science` | pubmed, arxiv | | | | | |

| engine | source | notes |
|---|---|---|
| github | github's repository search api | takes github's qualifiers: `topic:mcp language:rust stars:>100 pushed:>2026-01-01`; 30 searches a minute signed in, 10 anonymous |
| github_code | github's code search api | exact code; needs a github login (10 a minute) |
| grepapp | grep.app | exact code across a million github repositories, no login |
| codeberg | codeberg's api | forgejo repositories, most starred first |
| gitlab | gitlab.com's api | projects, most starred first |
| npm | the npm registry's search api | packages with version and weekly downloads |
| crates | crates.io's api | rust crates; sends a user-agent that names weltsuche, as crates.io asks of api clients |
| gitee, gitcode | startpage with `site:gitee.com` / `site:gitcode.com` | site engines (`SITE_ENGINES`): gitee's own search answers anonymous clients with nothing. they rest with startpage |
| hackernews | algolia's hacker news api | stories; a hit links to the discussion, the snippet names the story's link |
| stackexchange | the stack exchange api's excerpt search | stack overflow, or its ru, ja, pt and es edition by `lang`; links to the question, which `fetch` reads with its answers; 300 requests a day without a key, shared with `fetch` |
| qiita | qiita's search page (json inside) | japanese; best match first (the public api sorts by date) |
| zenn | zenn's search api | japanese articles |
| juejin, csdn | their search apis | chinese articles |
| v2ex | sov2ex | the chinese developer forum |
| habr | habr's search api | russian articles |
| velog | velog's graphql api | korean developer blogs |
| pubmed | ncbi's e-utilities | biomedical papers, most relevant first; `fetch` a hit for its abstract |
| arxiv | arxiv's export api (atom) | preprints; plain words must all appear, arxiv's own syntax (`ti:`, `au:`, `cat:`, `AND`) passes as it is |

### shop and stay engines

the `shopping` set is amazon and ebay, plus kleinanzeigen for `lang: "de"`;
airbnb is named on its own. they take `params`, one vocabulary mapped to each
site's own filters (the search tool lists them per engine):

| engine | params | what to know |
|---|---|---|
| amazon | `price_min`, `price_max`, `sort` (relevance, price_asc, price_desc, newest, rating) | the region picks the store (de: amazon.de, gb: amazon.co.uk, jp: amazon.co.jp); the session opens the store's front page first, since a cold one gets the automated-access page; `price_asc` lists accessories first, so give `price_min` too; sponsored hits are marked |
| ebay | `price_min`, `price_max`, `sort` (relevance, price_asc, price_desc, newest, ending), `condition` (new, used), `sold` | `sold: true` lists what sold and for how much; akamai's bot manager answers 403 after a few requests and ebay then rests an hour; meanwhile `site:ebay.de` on startpage finds listings |
| kleinanzeigen | `price_min`, `price_max`, `location` (postcode or town), `radius_km`, `sort` (newest, price_asc) | the place becomes the site's own id through its place suggestions; paid "top" ads come first in every order; ad pages read well through `fetch` |
| airbnb | `checkin`, `checkout` (yyyy-mm-dd), `adults`, `children`, `price_min`, `price_max` (per night), `room_type` (entire, private, shared, hotel) | the query is the place; prices are totals for the dates (per night without dates); result links carry the dates |

a filter an engine does not take is named in its status ("not used by this
engine"); a value that does not fit is refused with the values that would.

the github login: with `GITHUB_AUTH = "gh"` (the default) weltsuche runs
`gh auth token` once per process, keeps the token in memory and sends it to
api.github.com only. without the github cli, or logged out, github searches
anonymously and `github_code` answers "needs a github login"; `gh auth login`
once fixes that. `check` says which mode is active.

`site:` restrictions ride on a web engine's index and cost one request of it
each. a burst of them got startpage and duckduckgo to answer with captchas,
and baidu captchas `site:` queries outright, so the site engines are few and
the code engines use apis.

## how it stays welcome

- one chrome fingerprint (curl_cffi impersonation) with a persistent cookie
  jar per engine, an accept-language matching the query and a referer from
  the engine's own page. the same one person, every time.
- at least `ENGINE_GAP_S` seconds between two requests to the same engine,
  and between two page fetches from the same host, plus a random `JITTER_S`.
  the slot is claimed in `state.json` under a lock shared by every
  weltsuche process before the wait, so several claude code sessions cannot
  fire at the same moment; fetches from different hosts do not wait for each
  other. a request whose engine was parked while it waited is not sent.
- an api with a published limit is spaced by that limit instead, without
  jitter: github every 2 s signed in (30 searches a minute), every 6 s
  anonymous and for code search (10 a minute).
- cookie files in `cookies/` are shared too: before each request the
  file is merged into the session, and afterwards only what that request
  changed is written back, under the same kind of lock. a toll one session
  paid is not erased by another session's write.
- cookie lifetimes are counted on the server's clock, as chrome does: when the
  local clock is off, curl alone would drop a short-lived cookie on arrival.
  a cookie stored for one host stays host-only when it is read back, so the
  server can still replace or delete it.
- a 429 or a 5xx rests the engine `BREAKER_RATE_LIMIT_S` (or `Retry-After`,
  up to `BREAKER_BLOCK_S`); a captcha, challenge page or 403 rests it
  `BREAKER_BLOCK_S`, a redirect loop `BREAKER_RATE_LIMIT_S`, a network error
  `BREAKER_NETWORK_S`. any other 4xx rests nothing: the query caused it.
  refusal phrases only
  count on a page that yielded no results, so a snippet quoting "captcha" does
  not park an engine. a block is respected, never retried into. no proxy
  rotation, no captcha solving. the one challenge it does answer is anubis's
  proof-of-work toll (startpage, and pages `fetch` reads), which asks for
  compute, not for a human. javascript bot checks (reddit's) are not
  answered; such sites are read through their routes instead.
- a results page where no hit mentions the query is a decoy and is dropped,
  not cached. a parser that finds hits but no usable link reports markup
  drift, not "no results".
- results and fetched pages are cached `CACHE_TTL_S` in `cache/`.
- robots.txt is not consulted, the same as a browser.

## settings

`config.example.py` lists every key with its default and a comment. weltsuche
copies it to `config.py` in its data folder on first start, with every
setting commented out; edit that copy, and remove the `# ` in front of a
setting to change it. a setting left commented keeps the current default, so
updates never need an edit, and a key weltsuche does not know is reported by
`check`. a value of the wrong shape (a string where a number belongs, a
language table without its `"*"` entry) stops the start with one error that
names the key. there are no environment
variables and no secrets; the github login stays with the github cli.

| key | meaning |
|---|---|
| `ENGINES_BY_LANG` | engine list per language, `"*"` is the fallback |
| `ENGINE_SETS` | named engine lists per language (`code`, `codesearch`, `dev`, `packages`, `science`, `shopping`) a caller can pass in `engines` |
| `SITE_ENGINES` | one site searched through a web engine with `site:`: name -> (carrier engine, domain) |
| `GITHUB_AUTH` | `"gh"` borrows the github cli's login, `"none"` stays anonymous |
| `REGION_BY_LANG` | default country per language; a language not listed is searched without one |
| `DEFAULT_LANG` | language when the caller gives none |
| `MAX_RESULTS`, `PER_ENGINE_RESULTS` | merged results returned, results asked from each engine |
| `ENGINE_GAP_S`, `JITTER_S` | spacing between requests to one engine or one host, random extra wait |
| `BREAKER_RATE_LIMIT_S`, `BREAKER_BLOCK_S`, `BREAKER_NETWORK_S` | rest after a rate limit or server error, after a challenge page, after a network error |
| `CACHE_TTL_S` | cache lifetime, seconds |
| `TIMEOUT_S` | per request; a page download is stopped after it |
| `IMPERSONATE` | curl_cffi browser profile |
| `FETCH_MAX_CHARS`, `FETCH_MAX_BYTES` | default text per fetch reply, download cap (a pdf past it is refused) |
| `FRONTENDS_PREFERRED` | frontend instances to try first, per libredirect key (`"redlib"`, `"nitter"`, ...) |
| `LOG_LEVEL` | logs go to stderr and to `data/weltsuche.log`, rotated at start past 5 mb |

## works well with

weltsuche reads the public web without a browser, accounts or keys. these
fill what it leaves out (checked 2026-10-03; the install lines are copied
from each project's readme):

| tool | adds | key | install |
|---|---|---|---|
| [playwright mcp](https://github.com/microsoft/playwright-mcp) | a real browser: javascript-only pages, logins, forms | no | `claude mcp add playwright npx @playwright/mcp@latest` |
| [claude in chrome](https://code.claude.com/docs/en/chrome) | your own chrome with your logins | a paid claude plan | `claude --chrome` |
| [paper-search-mcp](https://github.com/openags/paper-search-mcp) | semantic scholar, openalex, crossref, core, europe pmc, biorxiv; open-access pdfs | no (optional ones raise limits) | `uv tool install paper-search-mcp`, then its readme's claude code skill |
| [context7](https://github.com/upstash/context7) | current, version-specific library docs | free key, 1,000 calls a month | `npx ctx7 setup` |
| [deepwiki](https://docs.devin.ai/work-with-devin/deepwiki-mcp) | a wiki and q&a over any public github repository | no | `claude mcp add -s user -t http deepwiki https://mcp.deepwiki.com/mcp` |
| [docling mcp](https://github.com/docling-project/docling-mcp) | pdf and office files to text with ocr, for scans weltsuche cannot read | no | `uvx --from docling-mcp docling-mcp-server --transport stdio` as an mcp server |
| [exa](https://github.com/exa-labs/exa-mcp-server) | neural search: finds pages by meaning | works without a key, rate-limited | `claude plugin install exa@claude-plugins-official` |

claude code's Read tool reads pdfs page by page too; ranges of pages need
poppler's `pdftoppm` on the PATH.

## how it is put together

why it is built this way, what every engine and site does to a plain client,
the decisions, the gotchas and the open issues: [knowledge.md](knowledge.md).

```
.claude-plugin/       plugin.json (the plugin, and how claude code starts its server: uv run ...
                      python -m weltsuche serve), marketplace.json (this repo as its marketplace)
skills/weltsuche/     the skill: SKILL.md (when and how claude uses the tools), operators.md and
                      sources.md (read only when needed)
pyproject.toml        dependencies; uv.lock pins them
config.example.py     every setting with its default
weltsuche/
  app.py              the command line and the server's entry point
  server.py           the five mcp tools, their error boundary, stdout hand-over
  models.py           every reply and state shape (pydantic), one definition each
  settings.py         config.py in the data folder, seeded from the example
  search.py           fan-out, merge, rank
  fetch.py            page loading, walls, extraction, pdf text, route racing, paging
  sites.py            walled sites: their apis and embeds, and the route table
  frontends.py        public frontend instances: list, parking, relevance
  text.py             charset detection, visible text, shared text helpers
  net.py              curl_cffi sessions, cookies, spacing, block detection, url vetting
  anubis.py           the anubis proof-of-work toll, both protocol generations
  state.py            breakers, spacing and cache on disk, locked and atomic
  runtime.py          the per-process bundle of the above
  ui.py               terminal output for the command line
  engines/            one module per engine (NAME, HOME, search()); common.py what they share,
                      __init__.py the registry and the wrapper that runs one engine
docs/reference.md     this file: tools, failures, routes, engines, settings
docs/knowledge.md     maintainer notes: engines, sites, decisions, gotchas, open issues
```

a test suite runs against saved result pages, one parser test per engine: when
an engine changes its markup, the matching test fails before a language
quietly goes silent. it is not part of this repository.
