"""startpage, google index.

startpage fronts its pages with anubis, a sha-256 proof-of-work toll
(difficulty 4 to 5 as measured: 65k to 1M hashes, one to several seconds in
python). `anubis.py` pays it the way a browser does. the auth cookie it buys
lived minutes when measured 2026-09-29, not the week it once did, so the toll
comes round often; a fresh session sometimes gets no challenge at all.

after the toll, startpage wants what its own form sends: a fresh `sc` code
scraped from the homepage form, a POST with the form fields, and a
`preferences` cookie with language and region. a request that gets the
captcha page (`/sp/captcha`) means startpage still thinks this is a bot; the
breaker parks the engine for an hour.
"""

import json
import time

from selectolax.parser import HTMLParser

from .. import anubis, net
from .common import ParseError, plain, settle

NAME = "startpage"
HOME = "https://www.startpage.com/"
URL = HOME + "sp/search"
CAPTCHA = HOME + "sp/captcha"

#: an `sc` code stays valid for a while; searxng reuses one for an hour.
SC_TTL_S = 3600

# startpage names its interface languages; these are the values its settings
# form uses for `language` / `lui`. unknown languages fall back to english,
# which still returns results in the query's language via google.
LANG = {
    "en": "english", "de": "deutsch", "fr": "francais", "es": "espanol", "it": "italiano",
    "nl": "nederlands", "pt": "portugues", "pl": "polski", "sv": "svenska", "da": "dansk",
    "nb": "norsk", "fi": "suomi", "tr": "turkce", "ru": "russian", "uk": "ukrainian",
    "he": "hebrew", "ar": "arabic", "ja": "japanese", "zh": "jiantizhongwen", "ko": "hangul",
    "cs": "czech", "hu": "hungarian", "ro": "romanian", "el": "greek", "vi": "vietnamese",
    "th": "thai", "id": "indonesian", "hi": "hindi", "fa": "persian", "bg": "bulgarian",
}

# `search_results_region` values from the same form. a language without a
# row here searches with region "all", which google then reads from the query.
REGION = {
    ("en", "us"): "en_US", ("en", "gb"): "en-GB_GB", ("en", "au"): "en_AU", ("en", "ca"): "en_CA",
    ("en", "in"): "en_IN", ("en", "ie"): "en_IE", ("en", "nz"): "en_NZ", ("en", "sg"): "en_SG",
    ("de", "de"): "de_DE", ("de", "at"): "de_AT", ("de", "ch"): "de_CH", ("fr", "fr"): "fr_FR",
    ("fr", "be"): "fr_BE", ("fr", "ca"): "fr_CA", ("fr", "ch"): "fr_CH", ("es", "es"): "es_ES",
    ("es", "mx"): "es_MX", ("es", "ar"): "es_AR", ("it", "it"): "it_IT", ("nl", "nl"): "nl_NL",
    ("nl", "be"): "nl_BE", ("pt", "br"): "pt-BR_BR", ("pt", "pt"): "pt_PT", ("pl", "pl"): "pl_PL",
    ("ru", "ru"): "ru_RU", ("ru", "by"): "ru_BY", ("uk", "ua"): "uk_UA", ("ja", "jp"): "ja_JP",
    ("ko", "kr"): "ko_KR", ("zh", "cn"): "zh-CN_CN", ("zh", "tw"): "zh-TW_TW", ("zh", "hk"): "zh-TW_HK",
    ("tr", "tr"): "tr_TR", ("cs", "cz"): "cs_CZ", ("sv", "se"): "sv_SE", ("da", "dk"): "da_DK",
    ("fi", "fi"): "fi_FI", ("nb", "no"): "no_NO", ("hu", "hu"): "hu_HU", ("ro", "ro"): "ro_RO",
    ("el", "gr"): "el_GR", ("ar", "eg"): "ar_EG", ("vi", "vn"): "vi_VN", ("id", "id"): "id_ID",
    ("hi", "in"): "hi_IN", ("bg", "bg"): "bg_BG", ("et", "ee"): "et_EE",
}

_sc = {"code": "", "at": 0.0}


async def _page(http, method, url, lang, **kw):
    """a startpage page, paying the anubis toll once if it is asked for.
    judged by status and url here; refusal phrases by `settle`, once the
    page is known to hold no results."""
    resp = await http.request(NAME, method, url, lang=lang, check=False, **kw)
    if anubis.detect(resp.text):
        await anubis.pay(http, NAME, str(resp.url), resp.text, lang)
        resp = await http.request(NAME, method, url, lang=lang, check=False, spaced=False, **kw)
        if anubis.detect(resp.text):
            raise net.Blocked("anubis challenge again right after paying it")
    if str(resp.url).startswith(CAPTCHA):
        raise net.Blocked(f"startpage captcha at {resp.url}")
    net.classify(resp, markers=False)
    return resp


async def _sc_code(http, lang):
    if _sc["code"] and time.time() - _sc["at"] < SC_TTL_S:
        return _sc["code"]
    resp = await _page(http, "GET", HOME, lang)
    node = HTMLParser(resp.text).css_first('form#search input[name="sc"]')
    if node is None or not node.attributes.get("value"):
        raise ParseError("no sc code in the startpage search form")
    _sc["code"] = node.attributes["value"]
    _sc["at"] = time.time()
    return _sc["code"]


def _preferences(ui_lang, region_code):
    prefs = [
        ("date_time", "world"), ("disable_family_filter", "moderate"), ("disable_open_in_new_window", "0"),
        ("enable_post_method", "1"), ("enable_proxy_safety_suggest", "1"), ("enable_stay_control", "1"),
        ("instant_answers", "1"), ("lang_homepage", "s/device/en/"), ("num_of_results", "10"),
        ("suggestions", "1"), ("wt_unit", "celsius"), ("language", ui_lang), ("language_ui", ui_lang),
        ("search_results_region", region_code),
    ]
    return "N1N".join(f"{k}EEE{v}" for k, v in prefs)


PAYLOAD_MARK = "React.createElement(UIStartpage.AppSerpWeb, {"


def parse(html):
    """results ride inside the react bootstrap call on the page. the object is
    cut with a balanced decoder: it contains "})" in string values, so a
    search for the closing bracket lands in the middle of it."""
    i = html.find(PAYLOAD_MARK)
    if i < 0:
        low = html.lower()
        if "no results" in low or "did not match any" in low:
            return []
        raise ParseError("no UIStartpage.AppSerpWeb payload on the page")
    data, _ = json.JSONDecoder().raw_decode(html[i + len(PAYLOAD_MARK) - 1:])
    regions = data.get("render", {}).get("presenter", {}).get("regions", {})
    mainline = regions.get("mainline", [])
    hits = []
    for block in mainline:
        if block.get("display_type") != "web-google":
            continue
        for item in block.get("results", []):
            hits.append({
                "title": plain(item.get("title")),
                "url": item.get("clickUrl") or item.get("url") or "",
                "snippet": plain(item.get("description")),
            })
    if not hits:
        # a query with no results carries a "notice-noresults-empty" block
        # (measured 2026-09-29); a payload with neither that nor a web-google
        # block is markup drift, which must not read as "no results"
        types = [str(block.get("display_type")) for block in mainline]
        if not any("noresults" in t for t in types):
            raise ParseError(f"no web-google block in the payload (blocks: {', '.join(types) or 'none'})")
    return hits


async def search(http, query, lang, region, n):
    ui_lang = LANG.get(lang, "english")
    sc = await _sc_code(http, lang)
    data = {
        "query": query, "cat": "web", "t": "device", "sc": sc, "with_date": "",
        "abd": "1", "abe": "1", "qsr": "all", "qadf": "moderate",
        "language": ui_lang, "lui": ui_lang, "segment": "startpage.udog",
    }
    resp = await _page(
        http, "POST", URL, lang, data=data, referer=HOME,
        extra_headers={"Origin": HOME.rstrip("/")},
        cookies={"preferences": _preferences(ui_lang, REGION.get((lang, region), "all"))},
    )
    return settle(resp, parse, query)[:n]
