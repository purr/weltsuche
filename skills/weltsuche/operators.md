# search operators and dorks

operators go to the engine as typed, through `search` and through WebSearch.
send one or two operator queries at a time through weltsuche: bursts of
`site:` and `filetype:` get startpage and duckduckgo to answer with captchas,
and baidu answers `site:` with a captcha outright (the engine then rests;
`engines` says so).

## web engines

from each engine's own help page; "checked" means it worked through weltsuche
on 2026-10-03.

| engine | operators | checked |
|---|---|---|
| `startpage` (google's index) | `"exact"`, `-word`, `site:`, `filetype:`, `before:` / `after:` (`2024`, `2024-05-01`); `intitle:`, `inurl:` work though google documents only the others | `filetype:pdf`, `site:`, `intitle:` |
| `duckduckgo` | `"exact"`, `-word`, `+word`, `site:`, `-site:`, `filetype:` (pdf, doc(x), xls(x), ppt(x), html), `intitle:`, `inurl:` | `filetype:pdf`, `intitle:` |
| `brave` | `"exact"`, `+` / `-`, `site:`, `filetype:` / `ext:`, `intitle:`, `inbody:`, `inpage:`, `lang:` (iso 639-1), `loc:` (country), `AND` / `OR` / `NOT` | |
| `bing` (no default) | `site:` (several with `OR`), `filetype:` / `ext:`, `intitle:`, `inbody:`, `inanchor:`, `contains:`, `language:`, `loc:`, `feed:` | |
| `yandex` | `site:`, `host:`, `url:` (`*` at the end), `mime:` (pdf, doc, xls, ppt, rtf, odt, ...), `lang:`, `date:` (`20250101`, `>20250101`, `20250101..20250601`, `2025*`) | |
| `baidu` | `"exact"`, `-word`, `intitle:`, `inurl:`, `filetype:`, 《书名号》 for a title; no `site:` | `filetype:pdf` |
| `naver`, `naver_blog` | `"exact"`, `site:` | `"헤드폰 앰프" site:blog.naver.com` |

## code, packages, papers, communities

| engine | syntax | example (checked) |
|---|---|---|
| `github` | `in:name,description,readme,topics`, `topic:`, `language:`, `stars:>N`, `forks:>N`, `pushed:>YYYY-MM-DD`, `created:`, `user:`, `org:`, `license:`, `archived:false`, `fork:true` | `topic:mcp language:python stars:>500` |
| `github_code` | `repo:O/R`, `user:`, `org:`, `path:`, `filename:`, `extension:`, `language:`, `in:file,path` | `filename:pyproject.toml fastmcp` |
| `stackexchange` | `[tag]`, `"phrase"` | `[python-asyncio] timeout` |
| `pubmed` | field tags `[ti]`, `[tiab]`, `[au]`, `[mh]` (mesh), `[pt]` (publication type), `[dp]` (date); `AND` / `OR` / `NOT` | `magnesium[ti] AND sleep[ti] AND systematic review[pt]` |
| `arxiv` | `ti:`, `au:`, `abs:`, `cat:`; `AND` / `OR` / `ANDNOT` | `ti:transformer AND cat:cs.CL` |
| `npm` | `keywords:`, `author:`, `maintainer:`, `scope:` | `keywords:mcp search` |
| `wikipedia` | `intitle:`, `insource:`, `incategory:` | `intitle:amplifier headphone` |

## dorks by purpose

| to find | query |
|---|---|
| a datasheet, manual or paper as pdf | `lm358 datasheet filetype:pdf`; yandex `lm358 mime:pdf`; baidu `lm358 数据手册 filetype:pdf` |
| the maker's own page | `lm358 site:ti.com` |
| reviews, not shops | `intitle:review "topping dx5"` |
| one forum's threads | `"dx5 ii" site:audiosciencereview.com`, `site:reddit.com/r/headphones dx5`, `"헤드폰 앰프" site:blog.naver.com` |
| slides and spreadsheets | `filetype:ppt`, `filetype:xls` |
| recent or old pages | google `after:2025-01-01`, `before:2020`; yandex `date:>20250101` |
| one language or country | brave `lang:de loc:at`; bing `language:de loc:AT` |
| without one site | `-site:pinterest.com` |
| active projects on github | `topic:mcp pushed:>2026-01-01 stars:>50` |
| a file pattern across github | `filename:Dockerfile playwright` (`github_code`) |
