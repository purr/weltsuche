"""the objects one process shares: config, http sessions, breakers, cache,
all kept in one data directory (see settings.py)."""

import logging
from pathlib import Path

from . import engines, net, state

log = logging.getLogger("weltsuche.runtime")


class Runtime:
    def __init__(self, config, data_dir):
        self.config = config
        self.data_dir = Path(data_dir)
        self.breakers = state.Breakers(self.data_dir / "state.json")
        self.cache = state.Cache(self.data_dir / "cache")
        self.http = net.Http(config, self.breakers, self.data_dir / "cookies")

    async def start(self):
        """the first work of a process: drop cache entries long past use."""
        pruned = await self.cache.prune(self.config.CACHE_TTL_S * 3)
        if pruned:
            log.info("cache: pruned %d stale entries", pruned)

    async def engine_breakers(self):
        """breaker state for the engines only; the state file also holds one
        spacing bucket per host that `fetch` has read. a site engine rests
        under its carrier's name, so the modules are all the names there are."""
        known = engines.MODULES
        return {name: b for name, b in (await self.breakers.all()).items() if name in known}

    async def close(self):
        await self.http.close()
