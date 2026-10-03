"""the command line, and the mcp server's entry point.

    python -m weltsuche serve                 the mcp server over stdio (what claude code starts)
    python -m weltsuche search WORDS...       one search, results as text
    python -m weltsuche fetch URL             one page, text as it would reach the model
    python -m weltsuche status                engines resting, cache size, where the data lives
    python -m weltsuche check                 prove the install works, change nothing

run from a checkout with `uv run python -m weltsuche ...`; uv builds the
environment on first use. `--data DIR` picks the data directory (the plugin
passes ${CLAUDE_PLUGIN_DATA}); `--debug` logs everything.
"""

import argparse
import asyncio
import logging
import logging.handlers
import os
import queue
import sys
from pathlib import Path

from . import engines, settings, ui
from .engines import github
from .fetch import fetch
from .runtime import Runtime
from .search import search
from .server import serve

REQUIRED = ("mcp", "curl_cffi", "trafilatura", "selectolax", "pypdf", "charset_normalizer", "pydantic")

# exit codes: 0 fine, 1 a check or a command failed, 2 the command line or
# the config file is wrong (argparse exits 2 for usage errors itself)
OK, FAILED, CONFIG = 0, 1, 2

# claude code keeps only the startup lines of an mcp server's stderr, so a
# failure mid-session used to leave nothing but a breaker's one-line reason
# (the startpage toll failures of 2026-09 could not be traced). every process
# appends to weltsuche.log in the data directory too; the file is rotated at
# start once it passes this size.
LOG_ROTATE_BYTES = 5_000_000


def _param(text):
    """NAME=VALUE as (name, value); anything else stops the command, where it
    was dropped without a word."""
    name, sep, value = text.partition("=")
    if not sep or not name.strip():
        raise argparse.ArgumentTypeError(f"{text!r} is not NAME=VALUE")
    return name.strip(), value


def _parser():
    p = argparse.ArgumentParser(prog="weltsuche", description="multilingual web search and page reading, as an mcp server for claude code")
    p.add_argument("--data", metavar="DIR", help=f"data directory: config.py, state, cache, cookies, log (default: {settings.DEFAULT_DATA})")
    p.add_argument("--debug", action="store_true", help="log everything")
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")
    sub.add_parser("serve", help="run the mcp server over stdio (claude code starts this)")
    s = sub.add_parser("search", help="one search, results as text")
    s.add_argument("words", nargs="+", help="the query, in the language of --lang")
    s.add_argument("--lang", help="iso 639-1 language, or a tag like zh-TW")
    s.add_argument("--region", help="iso 3166-1 alpha-2 country")
    s.add_argument("--engines", help="comma-separated engine names")
    s.add_argument("--max", type=int, help="merged results to show")
    s.add_argument("--param", action="append", default=[], type=_param, metavar="NAME=VALUE",
                   help="a filter for the engines that take one, e.g. price_max=300 (repeatable)")
    f = sub.add_parser("fetch", help="one page, its text as the model would get it")
    f.add_argument("url")
    f.add_argument("--lang", help="accept-language, and a youtube transcript's language")
    f.add_argument("--max-chars", type=int, help="characters to show")
    f.add_argument("--offset", type=int, default=0, help="where in the text to start")
    f.add_argument("--raw", action="store_true", help="the html source instead of the text")
    f.add_argument("--focus", help="only the passages that mention these words")
    sub.add_parser("status", help="engines resting, cache size, where the data lives")
    sub.add_parser("check", help="prove the install works, change nothing")
    return p


def _utf8_streams():
    """utf-8 on stdout and stderr: a cp1252 windows console raises on the
    first chinese or cyrillic character, from inside a logging call."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _setup_logging(config, debug, data_dir):
    """logs to stderr and to weltsuche.log, written by a listener thread: a
    log call on the event loop only queues the record, and the file and the
    pipe are written off the loop. returns the listener, which `run` stops,
    and so flushes, on the way out."""
    level = "DEBUG" if debug else str(getattr(config, "LOG_LEVEL", "INFO")).upper()
    log_file = data_dir / "weltsuche.log"
    try:
        if log_file.exists() and log_file.stat().st_size > LOG_ROTATE_BYTES:
            os.replace(log_file, log_file.with_name(log_file.name + ".1"))
    except PermissionError:
        pass  # another weltsuche process holds the file open (windows); a later start rotates it
    formatter = logging.Formatter("%(asctime)s %(process)d %(levelname).1s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    writers = [logging.StreamHandler(sys.stderr), logging.FileHandler(log_file, encoding="utf-8")]
    for writer in writers:
        writer.setFormatter(formatter)
    listener = logging.handlers.QueueListener(queue.SimpleQueue(), *writers)
    logging.basicConfig(handlers=[logging.handlers.QueueHandler(listener.queue)], level=level)
    for noisy in ("trafilatura", "htmldate", "charset_normalizer", "mcp"):
        logging.getLogger(noisy).setLevel(logging.ERROR if level != "DEBUG" else logging.INFO)
    listener.start()
    return listener


def _engine_problems(config):
    """(key, fault) for every engine name the config uses that does not
    exist, and for set or site engine entries of the wrong shape."""
    problems = []
    modules = engines.MODULES
    known = set(engines.names(config))
    for name, (carrier, domain) in config.SITE_ENGINES.items():
        if name in modules:
            problems.append((f"SITE_ENGINES[{name!r}]", "shadows the engine of the same name"))
        if carrier not in modules:
            problems.append((f"SITE_ENGINES[{name!r}]", f"carrier {carrier!r} is not a web engine: {', '.join(sorted(modules))}"))
        if not domain or "/" in domain:
            problems.append((f"SITE_ENGINES[{name!r}]", f"{domain!r} is not a bare domain"))
    groups = [("ENGINES_BY_LANG", config.ENGINES_BY_LANG)]
    for set_name, by_lang in config.ENGINE_SETS.items():
        if set_name in known:
            problems.append((f"ENGINE_SETS[{set_name!r}]", "has the name of an engine"))
        groups.append((f"ENGINE_SETS[{set_name!r}]", by_lang))
    for key, by_lang in groups:
        if "*" not in by_lang:
            problems.append((key, 'needs a "*" fallback entry'))
        for lang, names in by_lang.items():
            bad = [n for n in names if n not in known]
            if bad:
                problems.append((f"{key}[{lang!r}]", f"unknown engine(s): {', '.join(bad)}"))
    if config.GITHUB_AUTH not in ("gh", "none"):
        problems.append(("GITHUB_AUTH", f'{config.GITHUB_AUTH!r} is neither "gh" nor "none"'))
    return problems


def check(config, config_path, unknown, data_dir):
    """every requirement importable, the config sound, every configured
    engine known, the data directory writable. one line per check."""
    problems = []
    for name in REQUIRED:
        try:
            mod = __import__(name)
            ui.ok(name, getattr(mod, "__version__", "installed"))
        except ImportError as e:
            problems.append((name, f"not importable: {e}"))

    if unknown:
        problems.append(("config", f"{config_path} sets keys weltsuche does not know: {', '.join(unknown)}"))
    else:
        ui.ok("config", str(config_path))

    problems += _engine_problems(config)
    ui.ok("engines", ", ".join(sorted(engines.names(config))))
    if config.GITHUB_AUTH == "gh":
        signed_in = bool(asyncio.run(github.read_gh_token()))
        (ui.ok if signed_in else ui.warn)("github", "signed in through the gh cli" if signed_in else
                                          "gh cli missing or not logged in: anonymous, no github_code (gh auth login)")

    for name in ("ENGINE_GAP_S", "CACHE_TTL_S", "TIMEOUT_S", "BREAKER_NETWORK_S"):
        value = getattr(config, name)
        if value <= 0:
            problems.append((name, "must be > 0"))

    try:
        probe = data_dir / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        ui.ok("data", str(data_dir))
    except OSError as e:
        problems.append(("data", f"{data_dir} is not writable: {e}"))

    ui.ok("python", f"{sys.version.split()[0]} at {sys.executable}")
    for label, detail in problems:
        ui.fail(label, detail)
    ui.write("\n")
    if problems:
        ui.fail("Not ready", f"{len(problems)} problem(s)")
        return FAILED
    ui.ok("Ready", "every check passed")
    return OK


def _print_search(res):
    ui.info("query", f"{res.query!r} lang={res.lang} region={res.region}")
    for s in res.engines:
        line = f"{s.count} hits" + (" (cached)" if s.cached else "") + (f" — {s.note}" if s.note else "")
        (ui.ok if s.ok and s.count else ui.warn)(s.engine, line)
    ui.write("\n")
    for rank, r in enumerate(res.results, 1):
        ui.write(f"{rank:>2}. {r.title}\n    {r.url}\n    [{', '.join(r.engines)}] {r.snippet[:220]}\n")
    ui.write("\n")
    ui.info("summary", res.summary)


def _print_page(page):
    for key in ("url", "via", "status", "kind", "title", "author", "date", "sitename", "description",
                "chars_total", "chars_returned", "next_offset", "error"):
        value = getattr(page, key)
        if value not in (None, "", 0) or key in ("status", "chars_total"):
            (ui.fail if key == "error" else ui.info)(key, str(value))
    for note in page.notes:
        ui.info("note", note)
    for failure in page.tried:
        ui.warn("tried", failure)
    ui.write("\n" + page.text + "\n")


async def _command(args, config, data_dir):
    rt = Runtime(config, data_dir)
    await rt.start()
    try:
        if args.command == "serve":
            await serve(rt)
            return OK
        if args.command == "search":
            engines_arg = [e for e in args.engines.split(",") if e] if args.engines else None
            params = dict(args.param)
            _print_search(await search(rt, " ".join(args.words), args.lang, args.region, engines_arg, args.max, params))
            return OK
        if args.command == "fetch":
            page = await fetch(rt, args.url, args.lang, args.max_chars, args.offset, args.raw, args.focus)
            _print_page(page)
            return FAILED if page.error else OK
        # status
        ui.info("data", str(data_dir))
        ui.info("engines", ", ".join(engines.names(config)))
        ui.info("engine sets", ", ".join(config.ENGINE_SETS))
        for name, b in (await rt.engine_breakers()).items():
            (ui.warn if b.open else ui.ok)(name, f"resting {b.seconds_left}s: {b.reason}" if b.open else f"ok, last request {b.last_request_ago_s}s ago")
        c = await rt.cache.stats()
        ui.info("cache", f"{c['files']} entries, {c['bytes'] / 1e6:.1f} MB")
        return OK
    finally:
        if args.command != "serve":
            await rt.close()


def run(argv=None):
    args = _parser().parse_args(argv)
    _utf8_streams()
    data_dir = Path(args.data) if args.data else settings.DEFAULT_DATA
    try:
        config, config_path, unknown = settings.load(data_dir)
    except (settings.ConfigError, OSError) as e:
        sys.stderr.write(f"weltsuche: {e}\n")
        return CONFIG
    listener = _setup_logging(config, args.debug, data_dir)
    try:
        if args.command == "check":
            return check(config, config_path, unknown, data_dir)
        return asyncio.run(_command(args, config, data_dir))
    except KeyboardInterrupt:
        return OK
    finally:
        listener.stop()
