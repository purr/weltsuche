"""amazon's own search, per country: the region picks the store (de is
amazon.de, gb amazon.co.uk, jp amazon.co.jp, anything unknown amazon.com).

amazon answers a session it does not know with its automated-access page
(http 503) or a "continue shopping" interstitial: the session opens the
store's front page first, as a person arrives, and keeps the cookies it is
given there. a sponsored hit links through a click tracker, so every hit is
cited as the store's /dp/ page; the snippet says when it is an ad."""

from selectolax.parser import HTMLParser

from .common import ParseError, amount, settle, text

NAME = "amazon"
HOME = "https://www.amazon.com/"

STORES = {
    "us": "amazon.com", "de": "amazon.de", "at": "amazon.de", "ch": "amazon.de", "gb": "amazon.co.uk",
    "uk": "amazon.co.uk", "ie": "amazon.co.uk", "fr": "amazon.fr", "it": "amazon.it", "es": "amazon.es",
    "nl": "amazon.nl", "be": "amazon.com.be", "se": "amazon.se", "pl": "amazon.pl", "tr": "amazon.com.tr",
    "jp": "amazon.co.jp", "ca": "amazon.ca", "mx": "amazon.com.mx", "br": "amazon.com.br", "au": "amazon.com.au",
    "in": "amazon.in", "ae": "amazon.ae", "sa": "amazon.sa", "sg": "amazon.sg", "eg": "amazon.eg",
}
SORTS = {"relevance": "", "price_asc": "price-asc-rank", "price_desc": "price-desc-rank",
         "newest": "date-desc-rank", "rating": "review-rank"}
PARAMS = {"price_min": float, "price_max": float, "sort": tuple(SORTS)}

def store(region):
    return f"https://www.{STORES.get(region, 'amazon.com')}/"


def query_params(query, params):
    """the store's search parameters: prices in the store's currency, as its
    own price filter takes them."""
    out = {"k": query}
    if SORTS.get(params.get("sort", "")):
        out["s"] = SORTS[params["sort"]]
    for name, key in (("price_min", "low-price"), ("price_max", "high-price")):
        if name in params:
            out[key] = amount(params[name])
    return out


def parse(html, base=HOME):
    tree = HTMLParser(html)
    bar = tree.css_first('[data-component-type="s-result-info-bar"]')
    if bar is not None and not text(bar):
        # a search without matches: amazon leaves the bar that counts the
        # results empty and fills the page with unrelated products
        # (2026-10-03), which must not read as hits or as a decoy
        return []
    hits = []
    for item in tree.css('div[data-component-type="s-search-result"]'):
        asin = item.attributes.get("data-asin")
        if not asin:
            continue
        # some stores show the brand in an h2 of its own above the title
        title = max((text(h) for h in item.css("h2")), key=len, default="")
        reviews = text(item.css_first("a[href*='customerReviews'] span"))
        facts = [text(item.css_first(".a-price .a-offscreen")), text(item.css_first(".a-icon-alt")),
                 f"{reviews.strip('()')} ratings" if reviews else "",
                 "sponsored" if "AdHolder" in (item.attributes.get("class") or "") else ""]
        hits.append({"title": title, "url": f"{base}dp/{asin}", "snippet": " · ".join(f for f in facts if f)})
    if not hits and len(html) > 20000:
        raise ParseError('no div[data-component-type="s-search-result"] on a full page')
    return hits


async def _known(http, base, lang):
    """open the store's front page once, so the session carries the cookies
    amazon gives a person who arrives there."""
    domain = base.split("www.", 1)[1].rstrip("/")
    if not await http.has_cookie(NAME, "session-id", domain=domain):
        # the front page answers http 202 to a fresh session; what counts is
        # the cookies it sets, the search request after it is judged
        await http.get(NAME, base, lang=lang, check=False)


async def search(http, query, lang, region, n, params=None):
    base = store(region)
    await _known(http, base, lang)
    resp = await http.get(NAME, base + "s", lang=lang, referer=base, params=query_params(query, params or {}), check=False)
    return settle(resp, lambda html: parse(html, base), query)[:n]


def product_text(html):
    """(title, text) of a product page: the facts first (price, rating,
    availability), then the bullet points, the description and the details
    table."""
    tree = HTMLParser(html)
    title = text(tree.css_first("#productTitle"))
    if not title:
        return "", ""
    price = text(tree.css_first("#corePrice_feature_div .a-offscreen, #corePriceDisplay_desktop_feature_div .a-offscreen,"
                                " #apex_desktop .a-offscreen"))
    facts = [price, text(tree.css_first("#acrPopover .a-icon-alt")), text(tree.css_first("#acrCustomerReviewText")),
             text(tree.css_first("#availability span"))]
    bullets = [text(li) for li in tree.css("#feature-bullets li")]
    details = [" ".join(text(cell) for cell in row.css("th, td")) for row in tree.css(
        "#productDetails_techSpec_section_1 tr, #productDetails_detailBullets_sections1 tr")]
    details += [text(li) for li in tree.css("#detailBullets_feature_div li")]
    parts = [" · ".join(f for f in facts if f), "\n".join(f"- {b}" for b in bullets if b),
             text(tree.css_first("#productDescription")), "\n".join(d for d in details if d)]
    return title, "\n\n".join(p for p in parts if p)


async def product(http, url, lang):
    """a product page through the engine's session: amazon shows a session it
    does not know an interstitial instead of the product."""
    base = url.split("/", 3)
    base = f"{base[0]}//{base[2]}/"
    await _known(http, base, lang)
    resp = await http.get(NAME, url, lang=lang, referer=base, check=True)
    return product_text(resp.text)
