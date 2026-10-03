---
name: weltsuche
description: multilingual web, code and paper research with the weltsuche mcp tools (search, search_many, fetch, fetch_many), one aid next to WebSearch, WebFetch and the github cli. use it on your own when a task depends on facts outside this repository - a product, hardware, medicine, science, prices, what people report, a library's current api or changelog, a dependency's error message, a cve, prior art, how others implement something - and especially when the user says research, look up or compare, names another country or language, or WebSearch came back thin or us-only. not when the repository, its docs or the installed packages answer it.
---

# weltsuche

weltsuche is an aid, not a boundary. it adds what the built-in tools lack:

- `search` / `search_many`: a query in its own language across several
  engines at once (google through startpage, baidu, yandex, naver, duckduckgo,
  brave, wikipedia), merged, plus engine sets for code, packages, developer
  communities and papers.
- `fetch` / `fetch_many`: a public page as text, or several in one call,
  pdfs included, and walled sites through
  their apis or mirrors: reddit with its comments, x, youtube with its
  transcript, instagram, tiktok, threads, bluesky, telegram, stack exchange,
  naver blog, github repositories and files.

you decide what to use. WebSearch, WebFetch, the github cli and any other tool
of the session are equal options: take whichever reaches the answer, switch
when one comes back thin. write every query natively in its language; the
tools do not translate.

## depth

| depth | when | do |
|---|---|---|
| quick | a fact, price, spec, date, "does x exist" | one `WebSearch` or `search`; fetch a page only if the snippets disagree |
| normal | how something works, one product's specs and problems | english plus the language where the thing lives; 1 to 3 pages |
| deep | comparisons, buying, "what do people say", medicine, safety | several languages, 3 to 6 pages, forums and video reviews |

escalate one step only when the lighter pass is thin, contradictory or
one-sided.

## where to look

| task | way |
|---|---|
| this repository, installed libraries | Grep, Read, the language server, the library's source; no web search |
| docs, products, news, opinions | `WebSearch` plus `search_many` in the languages below |
| a library, tool or project | `engines: ["code"]` (github, codeberg, gitlab; gitee for zh), `["packages"]` (npm, crates.io) |
| where code, a call or an error message appears | `["codesearch"]`; `gh search code "..." --repo O/R` for repo or path limits |
| what developers write | `["dev"]` per language: hacker news, stack overflow and its ru/ja/pt/es editions, juejin, csdn, v2ex, qiita, zenn, velog, habr |
| papers | `["science"]`: pubmed, arxiv |
| products, prices, second-hand, stays | `["shopping"]` (amazon, ebay; kleinanzeigen for de) or `airbnb` with `params`: prices, sort, condition, `sold` (ebay: what things sold for), place and radius (kleinanzeigen), dates and guests (airbnb, the query is the place). the region picks the store. `fetch` a product, ad or room link for its facts |
| inside one github repository | the github cli: `gh api -H "Accept: application/vnd.github.raw" repos/O/R/contents/PATH`, `gh issue list -R O/R --search "..."`, `gh release list -R O/R` |

shop gotchas: amazon's `price_asc` lists accessories first, so give
`price_min`; ebay often rests after akamai's 403, then try `site:ebay.de`;
kleinanzeigen's paid top ads come first in every order.

code, package and paper indexes match keywords: 2 to 4 words (`mcp web
search`), not sentences; github qualifiers work (`topic:mcp language:rust
stars:>50`). for software, run `code` and `dev` in english and chinese at
least.

languages worth adding: audio and hardware zh, ja, de, ru; medicine zh, ru,
de, ja plus `["science"]`; software and security ru, zh; cars and machines de,
ja, zh; hebrew sources he; korean products ko (naver and naver blog answer by
default); otherwise the countries that make or use the thing. `zh-TW` and
`zh-HK` for taiwan and hong kong.

## method

1. frame the question in one line; pick the depth and the languages.
2. search in one parallel batch: `WebSearch` for english, `search_many` for
   english and the other languages, each query native. the two english sides
   use different indexes and fail at different times (reddit is closed to
   WebSearch; weltsuche lists it).
3. read the best urls across languages with one `fetch_many` call (each
   entry a full fetch: url, lang, focus); `fetch` is for a single page.
   never one `fetch` per url with a wait between them. for
   one fact in a long page or pdf, pass `focus` with a few words in the page's
   language: only the matching passages come back, marked `[at N]`
   (`offset=N` reads on). a video's transcript is its text; a reddit thread
   comes with its comments. cite `url`; mention `notes` when they limit a
   claim (`via` is what was read, `tried` what failed first).
4. not answered yet? work out where the answer lives and go there, with
   weltsuche or without it:
   - sites with no engine here: the forum, shop, database, registry, maker
     or archive the results keep pointing at. search it with `site:domain`
     through `search` or `WebSearch`, or `fetch` its own search page
     (`https://forum.example/search?q=...`) and then its threads. starting
     points by topic: [sources.md](sources.md).
   - primary sources: datasheets, manuals, changelogs, standards, papers,
     issue trackers (`gh search issues "..."`), often as pdfs (below).
   - other words and operators: the field's own terms, a model number, the
     error message in quotes, another language, `filetype:`, `intitle:`,
     `site:`, a date range. what each engine understands, with examples by
     purpose: [operators.md](operators.md).
   - old or vanished pages: `fetch("https://web.archive.org/web/2024/<url>")`.
   - other tools of the session: WebFetch (another network), browser or
     documentation tools.
   stop when it is found, or when more costs more than the question is worth,
   and say what stays unknown.
5. report with the url and its language on every claim, in the user's
   language; say where languages disagree and which engines rested when
   coverage was thin. when a site with no engine here carried the answer,
   name it in one line: it may be worth adding to weltsuche.

## pdfs

datasheets, manuals, papers, standards and reports are often pdfs: find them
with `filetype:pdf` (`mime:pdf` on yandex). `fetch` extracts the text layer
itself (the first 300 pages); `focus` picks one fact out of a long one.
"the pdf has no text layer" means a scan: download it into the scratchpad
(`curl -L -o`) and read it with the Read tool, which looks at the pages
themselves: up to 10 pages whole, longer pdfs in ranges of up to 20 pages
(that needs poppler's `pdftoppm`; without it the read says so). or find an
html version or another copy.

## errors

every failure says whether a retry helps; retry at most once.

| the reply says | do |
|---|---|
| an engine `resting Ns` (rate limit, server error, network error, block) | not that engine before N seconds; the others carry the query |
| `the engine rejected the query` | rephrase, or another engine |
| `nothing found by x` | rephrase, or another language |
| `needs a github login` | the user runs `gh auth login` once |
| `parser found nothing usable`, `a weltsuche bug` | other engines; no retry |
| fetch `network error, worth a retry later`, `retry in Ns` | once, later |
| fetch `refused`, `http 404`, `not a text document`, a pdf error | no retry |
| fetch `blocked`, `no readable text` | `WebFetch` once, or another source; quora and imdb have no route |
| fetch `partial: true` | incomplete for a passing reason; fetch again later if the gap matters |

results and pages are cached for a day (`cached: true`); errors and partial
pages are not. the server spaces its own requests: do not hammer.
