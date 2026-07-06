---
name: news-html-digest
description: Fetch Chinese and tech/finance news plus a gold price trend card, then generate a readable HTML digest named with Beijing time. Use when the user says "【新闻】", asks for a Codex-platform news skill, or requests a HTML news report from 黄金金价查询, 60 秒每日要闻, 微博热搜, 知乎热榜, 抖音热搜, 腾讯新闻科技, 澎湃新闻, 东方财富股票新闻, Bilibili, IT之家, 少数派, 掘金, 百度, 头条, Hacker News, 参考消息, Solidot, or 财联社.
---

# News HTML Digest

## Workflow

When the user says `【新闻】`, generate the report with the bundled script:

```bash
python <skill-dir>/scripts/generate_news_html.py --output-dir <target-dir>
```

- Use the current working directory as `<target-dir>` unless the user names another location.
- The script names files as `YYYYMMDD-HHMMSS.html`, where the timestamp is always Beijing time (UTC+8).
- Fetch as many items as allowed by the configured source limit. The default is `20`; allowed `--limit` values are `10` through `20`. Do not reduce the limit for normal user-facing reports.
- Render each valid API/source as one color-coded card, with that interface's returned news displayed as rows inside the card. Source chips at the top are clickable anchors to their cards, and the page includes a right-side back-to-top button.
- Automatically mark only short-title, low-density sources as compact cards and render their items in balanced left/right columns inside a full-width card. Split counts evenly, such as 20 items into 10 and 10, and never leave a visible empty column. Keep long-title, long-summary, or media-heavy sources full-width single-column.
- After writing the HTML, capture a same-basename long PNG screenshot of the opened page with local Edge/Chrome headless and place it next to the HTML file. Hide the back-to-top button only in the screenshot capture. Report both files to the user and show the PNG in the final response when available.
- Keep the generated HTML file as the primary output; hide failed, unrecognized, or empty sources from the HTML page. If screenshot capture fails, still return the HTML and state the screenshot failure plainly.

## Data Sources

The script queries these sources in order:

1. `GET https://api.jijinhao.com/quoteCenter/realTime.htm` and `GET https://api.jijinhao.com/quoteCenter/history.htm` for 黄金金价查询; uses `GET https://push2.eastmoney.com/api/qt/stock/get?secid=133.USDCNH` to convert USD/oz into CNY/g and render 实时 / 近一月 / 近三月 trend cards.
2. `GET https://60s.viki.moe/v2/60s`
3. `GET https://60s.viki.moe/v2/weibo`
4. `GET https://60s.viki.moe/v2/zhihu`
5. `GET https://60s.viki.moe/v2/douyin`
6. `POST https://i.news.qq.com/web_feed/getPCList`
7. `POST https://api.thepaper.cn/contentapi/nodeCont/getByNodeIdPortal`
8. `GET https://np-listapi.eastmoney.com/comm/web/getNewsByColumns` with `column=354` for 东方财富股票新闻 / 公司资讯
9. `GET https://s.search.bilibili.com/main/hotword`
10. `GET https://api.bilibili.com/x/web-interface/popular`
11. `GET https://www.ithome.com/list/`
12. `GET https://sspai.com/api/v1/article/tag/page/get`
13. `GET https://api.juejin.cn/content_api/v1/content/article_rank`
14. `GET https://top.baidu.com/board?tab=realtime`
15. `GET https://www.toutiao.com/hot-event/hot-board/`
16. `GET https://news.ycombinator.com/`
17. `GET http://china.cankaoxiaoxi.com/json/channel/{zhongguo,guandian,gj}/list.json`
18. `GET https://www.solidot.org/index.rss`
19. `GET https://www.cls.cn/v2/article/hot/list`
20. `GET https://www.cls.cn/v1/roll/get_roll_list`

## Reliability Notes

- Prefer the bundled script over rewriting fetch logic. It includes retries, timeouts, source-level error isolation, structured JSON parsing, HTML/RSS extraction, 财联社 request signing, HTML escaping, Edge/Chrome headless screenshot capture, and Pillow-based screenshot cropping when Pillow is installed.
- If one source fails, is blocked by the remote site, is not recognized, or returns no usable items, still generate the HTML report but omit that source from the page.
- The gold price entry is rendered as a dedicated gold price card, not as ordinary news rows. It uses spot gold data plus USDCNH conversion, then renders separate 实时 / 近一月 / 近三月 SVG trends. Keep it first so the report opens with the gold price lookup.
- For 澎湃新闻, the default node is `25950` (时事). Override it with `--paper-node-id` only when the user requests a different node.
- The Bilibili, 抖音热搜, IT之家, 少数派, 掘金, 百度热搜, 今日头条, Hacker News, 参考消息, Solidot, and 财联社 integrations were added after inspecting public source behavior. Vercel and Netlify dashboard URLs are deployment platforms, not public news-source catalogs.

## Useful Options

```bash
python <skill-dir>/scripts/generate_news_html.py --output-dir . --limit 20 --timeout 15 --retries 2
```

- `--limit`: maximum items per source, allowed `10` to `20`, default `20`.
- `--timeout`: seconds per request attempt.
- `--retries`: retries per source after the first attempt.
- `--paper-node-id`: 澎湃新闻 node id, default `25950`.
- `--no-screenshot`: skip PNG screenshot capture.
- `--screenshot-width`, `--screenshot-height`, `--screenshot-timeout`: tune long screenshot capture. Defaults are `1200`, `60000`, and `60`.
