# News HTML Digest

Codex skill for generating a Beijing-time HTML news digest from multiple Chinese, tech, finance, and developer-news sources. It writes a self-contained HTML report and can also capture a long PNG screenshot of the page.

## What It Does

- Fetches gold price data, Chinese hot lists, tech news, finance news, Hacker News, Solidot, and more.
- Generates a timestamped HTML file named `YYYYMMDD-HHMMSS.html` using Beijing time.
- Captures a same-basename long PNG screenshot when a local Edge/Chrome-compatible browser is available.
- Renders source cards with clickable source chips for quick navigation.
- Adds a right-side back-to-top button in the web page while hiding that button from generated screenshots.
- Uses source-level error isolation, so one failed source does not break the whole report.

## Install As A Codex Skill

Clone this repository into your Codex skills directory:

```powershell
git clone https://github.com/guliacer/news-html-digest.git "$env:USERPROFILE\.codex\skills\news-html-digest"
```

Restart Codex after installing or updating the skill so it can rediscover `SKILL.md`.

## Usage

From Codex, ask for a latest-news report, for example:

```text
【新闻】
```

or:

```text
生成最新新闻日报
```

The skill runs:

```powershell
python "$env:USERPROFILE\.codex\skills\news-html-digest\scripts\generate_news_html.py" --output-dir .
```

You can also run the script directly:

```powershell
python scripts\generate_news_html.py --output-dir . --limit 20 --timeout 15 --retries 2
```

## Options

```text
--output-dir PATH          Output directory for HTML and PNG files
--limit 10..20            Maximum items per source, default 20
--timeout SECONDS         Per-request timeout, default 15
--retries COUNT           Retries per source after the first attempt, default 2
--paper-node-id ID        The Paper node id, default 25950
--no-screenshot           Skip PNG screenshot capture
--screenshot-width PX     Browser screenshot width, default 1200
--screenshot-height PX    Browser screenshot height, default 60000
--screenshot-timeout SEC  Screenshot timeout, default 60
```

## Data Sources

The generator currently queries:

- Gold price lookup through Jijinhao quote APIs, with USD/CNH conversion from Eastmoney.
- 60 秒每日要闻, Weibo hot search, Zhihu hot list, and Douyin hot search through `60s.viki.moe`.
- Tencent tech news, The Paper, Eastmoney stock news, Bilibili hot search, Bilibili popular videos, IT之家, 少数派, 稀土掘金, Baidu hot search, Toutiao hot board, Hacker News, 参考消息, Solidot, and 财联社.

Failed, empty, or blocked sources are omitted from the page rather than shown as broken cards.

## Output

For a successful run, the output directory receives files like:

```text
20260706-081629.html
20260706-081629.png
```

The HTML file is the primary artifact. The PNG is a long screenshot of the rendered page. If screenshot capture fails, the HTML is still kept and the failure is reported plainly.

## Repository Layout

```text
SKILL.md
agents/openai.yaml
scripts/generate_news_html.py
```

## Notes

- The generated reports are intended as quick reading dashboards, not archival guarantees.
- Remote sites may change schema, block requests, or return empty data; source-level errors are isolated.
- The screenshot path uses local Edge/Chrome headless mode. Set `NEWS_HTML_BROWSER` to a browser executable path if auto-detection misses your browser.
