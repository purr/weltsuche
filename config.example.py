"""configuration for weltsuche: every key, its default, and what it does.

on first start weltsuche copies this file to config.py in its data
directory, with every setting commented out: ${CLAUDE_PLUGIN_DATA} when
installed as a claude code plugin (`python -m weltsuche status` prints
where), data/ when run from a checkout. edit that config.py, not this file:
remove the "# " in front of a setting to change it. a setting left commented
keeps the default below, so an update that adds or changes a default needs
no edit, and a key this file does not know is reported by
`python -m weltsuche check`.

there are no secrets and no environment variables; the defaults run as they
are.
"""

# ===========================================================================
# engines
# ===========================================================================

# which engines answer a search when the caller gives a language but no engine
# list. keys are iso 639-1 codes, "*" is the fallback for every other
# language. available engines:
#   startpage   google index. pays startpage's anubis proof-of-work toll when
#               asked (the cookie lives minutes); honours language and region
#   duckduckgo  bing index, html.duckduckgo.com, honours a region code
#   brave       own index, honours a country
#   baidu       china
#   yandex      russia; captchas sometimes, the breaker handles it
#   naver       korea's own search engine (web results)
#   naver_blog  naver's blog results: korean reviews and experience reports
#   wikipedia   the language edition's search api, structured and reliable
#   bing        bing index directly. NOT a default: serves decoy results to
#               clients it distrusts; the decoy check drops them
# code and developer engines (json apis, none is a default; reach them by
# name or through ENGINE_SETS below):
#   github       repositories; takes github qualifiers (topic:, language:,
#                stars:>100, pushed:>2026-01-01)
#   github_code  code on github (needs the github login, see GITHUB_AUTH)
#   grepapp      exact code search over a million github repositories
#   codeberg     codeberg.org repositories
#   gitlab       gitlab.com projects
#   npm          npm packages (javascript, typescript)
#   crates       rust crates on crates.io
#   hackernews   hacker news stories, through algolia
#   stackexchange stack overflow questions; ru, ja, pt and es search their
#                own stack overflow edition
#   qiita, zenn  japanese developer articles
#   juejin, csdn chinese developer articles
#   v2ex         chinese developer forum, through sov2ex
#   habr         russian developer articles
#   velog        korean developer blogs
#   pubmed       biomedical and life-science papers (english titles)
#   arxiv        preprints: physics, mathematics, computer science
# shop and stay engines (none a default; they take `params`: prices, sort,
# condition, sold, place, dates, guests; the search tool lists them):
#   amazon       the store of the region (de: amazon.de, gb: amazon.co.uk)
#   ebay         the site of the region; `sold` for what things sold for
#   kleinanzeigen  germany's classifieds; `location` and `radius_km`
#   airbnb       stays; the query is the place
# plus the site engines of SITE_ENGINES.
ENGINES_BY_LANG = {
    "*": ["startpage", "duckduckgo", "brave", "wikipedia"],
    "zh": ["baidu", "startpage", "duckduckgo", "wikipedia"],
    "ru": ["yandex", "startpage", "duckduckgo", "wikipedia"],
    "ko": ["naver", "naver_blog", "startpage", "wikipedia"],
}

# default region (iso 3166-1 alpha-2, lowercase) per language, for engines that
# take one. a caller can always pass `region` explicitly. a language not
# listed is searched without a region (every country).
REGION_BY_LANG = {
    "en": "us", "de": "de", "zh": "cn", "ru": "ru", "he": "il", "ja": "jp",
    "ko": "kr", "fr": "fr", "es": "es", "it": "it", "pt": "br", "ar": "sa",
    "tr": "tr", "pl": "pl", "nl": "nl", "uk": "ua", "cs": "cz", "sv": "se",
    "vi": "vn", "th": "th", "id": "id", "hi": "in", "fa": "ir", "el": "gr",
    "hu": "hu", "ro": "ro", "fi": "fi", "da": "dk", "nb": "no", "bg": "bg",
    "et": "ee", "ms": "my", "sk": "sk", "hr": "hr", "sr": "rs", "sl": "si",
    "lt": "lt", "lv": "lv", "bn": "bd", "ur": "pk",
}

DEFAULT_LANG = "en"

# engine sets: a name the caller can pass in `engines` instead of engine
# names, expanded by the search language ("*" is the fallback). engines:
# ["code"] with lang="zh" asks github and gitee. code, package and paper
# indexes match keywords, not sentences: 2 to 4 words work best.
ENGINE_SETS = {
    # repositories and projects
    "code": {"*": ["github", "codeberg", "gitlab"], "zh": ["github", "gitee"]},
    # exact code: identifiers, calls, error messages
    "codesearch": {"*": ["github_code", "grepapp"]},
    # what developers write and discuss, in their own language
    "dev": {
        "*": ["hackernews", "stackexchange"],
        "zh": ["juejin", "csdn", "v2ex"],
        "ja": ["qiita", "zenn", "stackexchange"],
        "ko": ["velog"],
        "ru": ["habr", "stackexchange"],
        "pt": ["stackexchange"],
        "es": ["stackexchange"],
    },
    # libraries in the package registries
    "packages": {"*": ["npm", "crates"]},
    # papers: medicine and biology, physics, mathematics, computer science
    "science": {"*": ["pubmed", "arxiv"]},
    # products and second-hand offers; the region picks the store
    "shopping": {"*": ["amazon", "ebay"], "de": ["amazon", "ebay", "kleinanzeigen"]},
}

# site engines: one site searched through a web engine's index with a site:
# restriction, for sites whose own search is closed to plain clients.
# name -> (carrying engine, domain). the carrier's spacing and rest apply,
# and every one of these costs a carrier request: bursts of site: queries
# get startpage and duckduckgo to answer with captchas, and baidu captchas
# site: queries outright, so keep this list short.
SITE_ENGINES = {
    "gitee": ("startpage", "gitee.com"),      # china's largest code host
    "gitcode": ("startpage", "gitcode.com"),  # csdn's code host, many mirrors
}

# the github login the github engines use. "gh" borrows the github cli's
# login (`gh auth token`, read once per process, kept in memory, sent to
# api.github.com only): 30 searches a minute and code search. "none" stays
# anonymous: 10 a minute, no code search.
GITHUB_AUTH = "gh"

# results returned per search after merging engines, and how many each engine
# is asked for.
MAX_RESULTS = 10
PER_ENGINE_RESULTS = 10


# ===========================================================================
# politeness / anti-block
# ===========================================================================

# minimum seconds between two requests to the same engine, and between two
# page fetches from the same host, shared across every running weltsuche
# process through data/state.json. fetches from different hosts do not wait
# for each other.
ENGINE_GAP_S = 5.0
# api engines with a published limit space themselves by it instead, without
# jitter: github 2 s signed in (30 searches a minute), 6 s anonymous and for
# code search (10 a minute). see weltsuche/engines/github.py.

# random extra wait before every request, seconds (min, max). a person does not
# fire requests at exact intervals.
JITTER_S = (0.4, 1.6)

# how long an engine stays off after a 429 / rate-limit signal, and after a
# captcha or challenge page. a block is respected, never retried into.
BREAKER_RATE_LIMIT_S = 600
BREAKER_BLOCK_S = 3600
# how long an engine stays off after a network error (timeout, dns, reset):
# long enough that a dead engine does not add TIMEOUT_S to every search,
# short enough that a passing fault is soon forgotten
BREAKER_NETWORK_S = 120

# search results and fetched pages are cached this long, seconds.
CACHE_TTL_S = 86400

# per-request timeout, seconds.
TIMEOUT_S = 25

# curl_cffi browser profile. "chrome" is the newest chrome the library knows.
# other values: "chrome124", "safari", "firefox", "edge".
IMPERSONATE = "chrome"


# ===========================================================================
# fetch
# ===========================================================================

# default number of characters of extracted text a fetch returns; the caller
# pages with `offset`.
FETCH_MAX_CHARS = 12000

# the download stops at this many bytes. an html page is read up to the cut;
# a pdf past it is refused, since a cut pdf cannot be parsed. datasheets and
# papers run to 20 or 30 mb.
FETCH_MAX_BYTES = 32_000_000


# ===========================================================================
# frontends
# ===========================================================================

# public frontend instances to try first, per libredirect frontend key
# ("redlib" for reddit, "nitter" for x, "invidious" for youtube, "kittygram"
# for instagram, "libMedium" for medium). the rest come from libredirect's
# daily list; see weltsuche/frontends.py.
FRONTENDS_PREFERRED = {
    "redlib": ["https://redlib.catsarch.com"],
}


# ===========================================================================
# logging
# ===========================================================================

# DEBUG, INFO, WARNING, ERROR. logs go to stderr; stdout is the mcp channel.
LOG_LEVEL = "INFO"
