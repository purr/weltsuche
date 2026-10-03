# weltsuche

multilingual web search and page reading for claude code, as a plugin: an mcp
server plus the skill that tells claude how to use it.

- search per language and region: google (through startpage), baidu, yandex,
  naver, duckduckgo, brave and wikipedia, merged and ranked.
- read pages with a browser fingerprint: articles, pdfs, legacy charsets,
  anubis-gated sites. walled sites (reddit, x, youtube with transcripts,
  instagram, tiktok, threads, bluesky, telegram, stack exchange, naver blog)
  are read through their apis, embeds or public frontends.
- search code, packages, developer communities and papers: github, gitee,
  codeberg, gitlab, grep.app, npm, crates.io, hacker news, stack overflow,
  juejin, csdn, v2ex, qiita, zenn, velog, habr, pubmed, arxiv.
- search shops and stays with their own filters: amazon, ebay, kleinanzeigen,
  airbnb.
- `fetch` with `focus` returns only the passages of a long page or pdf that
  mention a few words.
- no accounts, no api keys. github code search borrows the github cli's login
  when there is one.

claude does the translating: the question can arrive in english, and claude
writes the queries in chinese, russian, korean, hebrew or japanese itself. the
skill tells it when a topic is worth another language, how deep to go, and to
keep using its other tools alongside.

## install

you need [claude code](https://code.claude.com/docs/en/setup) and
[uv](https://docs.astral.sh/uv/). uv brings python and every dependency
itself: no venv, no pip, no config to write.

1. install uv, unless `uv --version` already answers. these are uv's own
   installers.

   macos and linux:

   ```sh
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

   windows, in powershell:

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

   then open a new terminal, so that `uv` is on the PATH.

2. in the folder that holds the `weltsuche` checkout, add it to claude code
   and build its environment once (all platforms):

   ```sh
   claude plugin marketplace add ./weltsuche
   claude plugin install weltsuche@weltsuche
   claude mcp list
   ```

   `claude mcp list` starts the server once, which builds its environment
   (about 15 seconds the very first time) and shows
   `plugin:weltsuche:weltsuche ... Connected`. without it, the first session
   may start before the environment is ready.

3. start a new claude code session (in a running one: `/reload-plugins`).

on first start weltsuche creates its `config.py` with defaults that work.

## update and uninstall

- after `git pull` (or any edit): `/reload-plugins` in a session, or a new
  session. claude code loads this plugin in place from the checkout, so no
  version bump or reinstall is needed.
- uninstall: `claude plugin uninstall weltsuche@weltsuche`, then
  `claude plugin marketplace remove weltsuche`. uninstalling deletes the plugin's
  data folder (config, cache, cookies) unless you pass `--keep-data`.

## use it from a terminal

in this folder (uv builds the environment on first use):

```
uv run python -m weltsuche search "topping dx5 ii review" --lang en
uv run python -m weltsuche search "拓品 DX5 II 评测" --lang zh
uv run python -m weltsuche search "thinkpad t14" --engines shopping --lang de --param price_max=400
uv run python -m weltsuche fetch https://youtu.be/dQw4w9WgXcQ --lang en
uv run python -m weltsuche status
uv run python -m weltsuche check
```

`search` takes `--lang`, `--region`, `--engines a,b`, `--max n`,
`--param name=value` (repeatable); `fetch` takes `--lang`, `--max-chars n`,
`--offset n`, `--raw`, `--focus "words"`. `--data DIR` picks another data
folder, `--debug` logs everything.

## if something does not work

| symptom | what to do |
|---|---|
| `/mcp` shows weltsuche as failed right after installing | the first start builds the environment; run `claude mcp list` once in a terminal, or reconnect it in `/mcp` |
| `uv: command not found` in the mcp log | open a new terminal (uv's folder joins PATH there) and restart claude code |
| a site comes back with `error` | read `tried` in the reply: every route and why it failed. `uv run python -m weltsuche fetch URL` shows the same in a terminal |
| engines `resting` | a rate limit or captcha; they come back on their own. `uv run python -m weltsuche status` lists them |
| anything else | `data/weltsuche.log` (or `weltsuche.log` in the plugin's data folder, `~/.claude/plugins/data/weltsuche-weltsuche`) has every request and every traceback |

## configuring

`config.example.py` lists every setting with its default and a comment.
weltsuche copies it to `config.py` in its data folder on first start, with
every setting commented out; remove the `# ` in front of a setting to change
it. `uv run python -m weltsuche check` names keys weltsuche does not know; a
value of the wrong shape stops the start with an error that names the key.
there are no environment variables and no secrets.

## more

- [docs/reference.md](docs/reference.md): the tools and their failures, how
  pages and walled sites are read, every engine and its filters, how
  weltsuche stays welcome, every setting, tools that work well with it, and
  the repository layout.
- [docs/knowledge.md](docs/knowledge.md): maintainer notes: why it is built
  this way, what every engine and site does to a plain client, the gotchas
  and the open issues.
