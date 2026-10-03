"""html and bytes to readable text: charset detection the way a browser does
it, the visible text of a page, and small helpers the page reader and the site
readers share."""

import codecs
import re

import charset_normalizer
from selectolax.parser import HTMLParser

_JUNK = ["script", "style", "noscript", "svg", "nav", "footer", "header", "iframe", "template"]
_BLOCKS = ("p, div, li, tr, h1, h2, h3, h4, h5, h6, pre, blockquote, section, article, main, aside, "
           "table, dd, dt, br, hr, figcaption, caption, form")

# stand-ins for <pre> blocks while whitespace is folded; private-use code
# points never occur in real text
_PRE_OPEN, _PRE_CLOSE = chr(0xE000), chr(0xE001)
_PRE = re.compile(f"{_PRE_OPEN}(\\d+){_PRE_CLOSE}")

_HEADER_CHARSET = re.compile(r"charset\s*=\s*[\"']?\s*([\w.:-]+)", re.I)
_META_CHARSET = re.compile(rb"<meta[^>]+charset\s*=\s*[\"']?\s*([\w.:-]+)", re.I)

# the whatwg encoding standard maps these labels to a superset, and every
# browser decodes with the superset; python's strict codec of the same name
# rejects bytes real pages contain
_CHARSET_ALIASES = {
    "shift_jis": "cp932", "shift-jis": "cp932", "sjis": "cp932", "x-sjis": "cp932",
    "ms_kanji": "cp932", "windows-31j": "cp932", "csshiftjis": "cp932",
    "gb2312": "gb18030", "gbk": "gb18030", "x-gbk": "gb18030", "gb_2312-80": "gb18030",
    "chinese": "gb18030", "csgb2312": "gb18030",
    "euc-kr": "cp949", "ks_c_5601-1987": "cp949", "big5": "big5hkscs",
    "iso-8859-1": "cp1252", "latin1": "cp1252", "ascii": "cp1252", "us-ascii": "cp1252",
    "iso-8859-9": "cp1254", "tis-620": "cp874",
}


def _codec(label):
    """the python text codec for a charset label, or None for a label that is
    unknown or not a text encoding ("hex", "base64" are bytes-to-bytes codecs
    in python and would raise on decode). a browser skips such a label too."""
    label = (label or "").strip().lower()
    if not label:
        return None
    try:
        info = codecs.lookup(_CHARSET_ALIASES.get(label, label))
    except LookupError:
        return None
    return info.name if getattr(info, "_is_text_encoding", True) else None


def decode_html(body, content_type):
    """bytes to text, picking the charset the way a browser does: byte order
    mark, then the http header, then a <meta> declaration in the first 4 kb,
    then utf-8 if the bytes are valid utf-8, then a statistical guess."""
    if body.startswith(codecs.BOM_UTF8):
        return body[len(codecs.BOM_UTF8):].decode("utf-8", "replace")
    if body.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return body.decode("utf-16", "replace")
    header = _HEADER_CHARSET.search(content_type or "")
    meta = _META_CHARSET.search(body[:4096])
    for label in (header and header.group(1), meta and meta.group(1).decode("ascii", "replace")):
        codec = _codec(label)
        if codec:
            return body.decode(codec, "replace")
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        pass  # undeclared and not utf-8: the guess below decides
    best = charset_normalizer.from_bytes(body[:200_000]).best()
    return body.decode(best.encoding if best else "utf-8", "replace")


def is_html(ctype, body):
    if "html" in ctype or "xml" in ctype:
        return True
    return not ctype and body.lstrip()[:15].lower().startswith((b"<!doctype html", b"<html"))


def is_text(ctype):
    return not ctype or ctype.startswith("text/") or ctype.endswith(("json", "javascript"))


def _inside_pre(node):
    parent = node.parent
    while parent is not None:
        if parent.tag == "pre":
            return True
        parent = parent.parent
    return False


def visible_text(html):
    """the text a reader sees, one line per block element, without scripts,
    navigation and page chrome. <pre> blocks (code in an answer, an rfc's
    diagrams) keep their own line breaks and indentation."""
    tree = HTMLParser(html)
    tree.strip_tags(_JUNK, recursive=True)
    body = tree.body or tree.root
    if body is None:
        return ""
    pres = []
    for node in [n for n in body.css("pre") if not _inside_pre(n)]:
        pres.append(node.text(deep=True, separator="", strip=False).strip("\n"))
        node.replace_with(f"{_PRE_OPEN}{len(pres) - 1}{_PRE_CLOSE}")
    for node in body.css(_BLOCKS):
        node.insert_after("\n")
    lines = (" ".join(line.split()) for line in body.text(separator=" ").split("\n"))
    text = "\n".join(line for line in lines if line)
    return _PRE.sub(lambda m: "\n" + pres[int(m.group(1))] + "\n", text) if pres else text


def tidy(text):
    """trailing spaces off each line, runs of blank lines down to one."""
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line.rstrip() for line in text.splitlines())).strip("\n")


def head_meta(html):
    """title and description straight from <head>, for pages the extractor
    gave up on."""
    tree = HTMLParser(html[:200_000])
    title = tree.css_first("title")
    desc = tree.css_first('meta[name="description"]') or tree.css_first('meta[property="og:description"]')
    return {
        "title": " ".join(title.text().split()) if title else "",
        "description": (desc.attributes.get("content") or "").strip() if desc else "",
    }
