"""configuration and where things live.

config.example.py (committed, at the repository root) holds every key with
its default and a comment. changed values go in config.py inside the data
directory, which first start creates as a copy of the example with every
setting commented out: a key that is not set there keeps the example's
current default, so an update that adds or changes a default needs no edit.
nothing is read from environment variables.

the data directory holds config.py, state.json, cache/, cookies/ and the log.
as a claude code plugin it is ${CLAUDE_PLUGIN_DATA}, which survives plugin
updates (plugin.json passes it with --data); run from a checkout it defaults to
data/ in the checkout.
"""

import ast
import importlib.util
import logging
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

log = logging.getLogger("weltsuche.settings")

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "config.example.py"
DEFAULT_DATA = ROOT / "data"


class ConfigError(Exception):
    """config.py cannot be loaded; the message names the file and the fault."""


def _keys(path):
    """the upper-case names a config file defines."""
    spec = importlib.util.spec_from_file_location(f"weltsuche_config_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ConfigError(f"{path}: not a python file")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:  # any fault in a user's config file is reported as one clear error
        raise ConfigError(f"{path} does not load: {type(e).__name__}: {e}") from e
    return {k: v for k, v in vars(module).items() if k.isupper()}


def _seed(path):
    """config.py as the example with every setting commented out, so that
    every key and its comment are in front of the user and none of them is
    set. a plain copy set every key, and an install then never saw a changed
    default (korean searches never got naver, 2026-10-03)."""
    source = EXAMPLE.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) and t.id.isupper() for t in node.targets):
            for i in range(node.lineno - 1, node.end_lineno or node.lineno):
                lines[i] = "# " + lines[i]
    # temp file, then replace: two sessions starting at once, or a kill
    # mid-write, must not leave a cut config.py that fails every later start
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".py")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("".join(lines))
    os.replace(tmp, path)


def _fits(value, template):
    """whether `value` has the shape of the example's default `template`:
    the same kind of value, a dict whose values fit its first value, a list
    whose items fit its first item, a tuple of the same length."""
    if isinstance(template, bool) or isinstance(value, bool):
        return isinstance(value, bool) and isinstance(template, bool)
    if isinstance(template, (int, float)):
        return isinstance(value, (int, float))
    if isinstance(template, tuple):
        return isinstance(value, (tuple, list)) and len(value) == len(template) and all(map(_fits, value, template))
    if isinstance(template, list):
        return isinstance(value, (list, tuple)) and (not template or all(_fits(v, template[0]) for v in value))
    if isinstance(template, dict):
        sample = next(iter(template.values()), None)
        return isinstance(value, dict) and all(isinstance(k, str) and (sample is None or _fits(v, sample))
                                               for k, v in value.items())
    return isinstance(value, type(template))


def _faults(own, defaults):
    """(key, fault) for every value of config.py that would fail far from
    its cause: a string where a number belongs failed every search with a
    TypeError (found in review 2026-10-03)."""
    for key, value in own.items():
        if key in defaults and not _fits(value, defaults[key]):
            yield key, f"has the wrong shape; the default is {defaults[key]!r}"
    tables = [("ENGINES_BY_LANG", own.get("ENGINES_BY_LANG"))]
    if isinstance(own.get("ENGINE_SETS"), dict):
        tables += [(f"ENGINE_SETS[{name!r}]", table) for name, table in own["ENGINE_SETS"].items()]
    for key, table in tables:
        if isinstance(table, dict) and "*" not in table:
            yield key, 'needs a "*" entry, the fallback for every other language'


def load(data_dir):
    """(config, path of the user's config.py, keys in it that the example
    does not know). creates the data directory and config.py when missing."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "config.py"
    if not path.exists():
        _seed(path)
        log.info("settings: created %s, every setting commented out", path)
    defaults, own = _keys(EXAMPLE), _keys(path)
    faults = [f"{key} {fault}" for key, fault in _faults(own, defaults)]
    if faults:
        raise ConfigError(f"{path}: " + "; ".join(faults))
    unknown = sorted(own.keys() - defaults.keys())
    if unknown:
        log.warning("settings: %s sets keys weltsuche does not know: %s", path, ", ".join(unknown))
    return SimpleNamespace(**{**defaults, **own}), path, unknown
