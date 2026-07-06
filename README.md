# 新闻 HTML 日报

用于生成北京时间 HTML 新闻日报的 Codex skill。它会从中文热榜、科技新闻、财经新闻、开发者资讯和黄金金价等多个来源抓取内容，输出一份可直接阅读的自包含 HTML 报告，并可同步生成页面长截图。

English documentation is preserved below under [English](#english).

## 功能概览

- 抓取黄金金价、中文热榜、科技新闻、财经新闻、Hacker News、Solidot 等多源内容。
- 按北京时间生成带时间戳的 HTML 文件，文件名格式为 `YYYYMMDD-HHMMSS.html`。
- 当本机存在 Edge/Chrome 兼容浏览器时，自动生成同名 PNG 长截图。
- 将各数据源渲染为卡片，并在页面顶部提供可点击的数据源快捷入口。
- 在网页右侧提供返回顶部按钮，截图时会自动隐藏该按钮。
- 对每个数据源做独立错误隔离，单个来源失败不会影响整份日报生成。

## 安装为 Codex Skill

将仓库克隆到 Codex skills 目录：

```powershell
git clone https://github.com/guliacer/news-html-digest.git "$env:USERPROFILE\.codex\skills\news-html-digest"
```

安装或更新后重启 Codex，让 Codex 重新发现 `SKILL.md`。

## 使用方式

在 Codex 中直接请求最新新闻，例如：

```text
【新闻】
```

或：

```text
生成最新新闻日报
```

该 skill 默认运行：

```powershell
python "$env:USERPROFILE\.codex\skills\news-html-digest\scripts\generate_news_html.py" --output-dir .
```

也可以直接运行脚本：

```powershell
python scripts\generate_news_html.py --output-dir . --limit 20 --timeout 15 --retries 2
```

## 参数

```text
--output-dir PATH          HTML 和 PNG 文件输出目录
--limit 10..20            每个来源最多抓取的条目数，默认 20
--timeout SECONDS         单次请求超时时间，默认 15
--retries COUNT           首次请求失败后的重试次数，默认 2
--paper-node-id ID        澎湃新闻节点 ID，默认 25950
--no-screenshot           跳过 PNG 截图生成
--screenshot-width PX     浏览器截图宽度，默认 1200
--screenshot-height PX    浏览器截图高度，默认 60000
--screenshot-timeout SEC  截图超时时间，默认 60
```

## 数据源

当前生成器会查询：

- 通过金金号报价接口获取黄金金价，并使用东方财富 USD/CNH 汇率进行换算。
- 通过 `60s.viki.moe` 获取 60 秒每日要闻、微博热搜、知乎热榜和抖音热搜。
- 腾讯新闻科技、澎湃新闻、东方财富股票新闻、Bilibili 热搜、Bilibili 热门视频、IT之家、少数派、稀土掘金、百度热搜、今日头条热榜、Hacker News、参考消息、Solidot 和财联社。

失败、为空或被拦截的数据源会从页面中省略，不会显示为损坏卡片。

## 输出

成功运行后，输出目录会得到类似文件：

```text
20260706-081629.html
20260706-081629.png
```

HTML 是主要产物。PNG 是渲染页面的长截图；如果截图失败，HTML 仍会保留，并在命令输出中明确说明截图失败原因。

## 目录结构

```text
SKILL.md
agents/openai.yaml
scripts/generate_news_html.py
```

## 注意事项

- 生成的日报适合快速阅读与浏览，不承诺作为长期归档数据源。
- 远端站点可能调整结构、拦截请求或返回空数据；脚本会按来源隔离错误。
- 截图依赖本机 Edge/Chrome headless 模式。如自动检测不到浏览器，可通过 `NEWS_HTML_BROWSER` 指定浏览器可执行文件路径。

## English

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
