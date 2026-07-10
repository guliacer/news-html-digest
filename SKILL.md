---
name: news-html-digest
description: >-
  用于生成北京时间 HTML 新闻日报：抓取黄金金价、中文热榜、科技/财经/开发者新闻，并输出可读 HTML 报告，可按用户保存偏好同时生成页面长截图。默认用于用户说“【新闻】”、请求最新新闻日报，或需要来自黄金金价查询、60 秒每日要闻、微博热搜、知乎热榜、抖音热搜、腾讯新闻科技、澎湃新闻、东方财富股票新闻、Bilibili、IT之家、少数派、掘金、百度、头条、Hacker News、参考消息、Solidot、财联社等来源的 HTML 新闻报告。
---

# 新闻 HTML 摘要

## 工作流程

When the user says `【新闻】`, generate the report with the bundled script:

```bash
python <skill-dir>/scripts/generate_news_html.py --output-dir <target-dir>
```

- Use the current working directory as `<target-dir>` unless the user names another location.
- The script names files as `YYYYMMDD-HHMMSS.html`, where the timestamp is always Beijing time (UTC+8).
- Fetch as many items as allowed by the configured source limit. The default is `20`; allowed `--limit` values are `10` through `20`. Do not reduce the limit for normal user-facing reports.
- Before the first normal run, check saved screenshot preference with `python <skill-dir>/scripts/generate_news_html.py --print-preferences`. If `screenshot_after_html` is `null`, ask the user: `是否在生成 HTML 后也生成页面长截图？另外，长截图功能会自动调用本地浏览器进行截图；如果有短暂的窗口闪烁或者停留，属于正常情况。我会记住这个选择，后续默认按此执行。`
- If the user wants screenshots, run the report once with `--remember-screenshot-preference yes`; if not, run it once with `--remember-screenshot-preference no`. On later uses, do not ask again; run the normal command and let the saved preference decide. Override only when the user explicitly asks for a different behavior in the current request.
- 将每个有效的API/source作为一张色码卡来渲染,该界面的回回新闻在卡片内以行显示. 上方的源芯片是可点击的锚到他们的卡上,页面包括了右侧回上方的按钮.
- 自动将仅短标题,低密度来源标记为紧凑卡片并让其物品在全宽卡片内的平分左/右列. 平分计数,如20个项目为10个和10个,从不留下明显的空栏. 保持长片标题,长片摘要,或媒体重源全线单列.
- 当保存偏好或本次显式参数要求截图时,在写入 HTML 后调用本地 Edge/Chrome 抓取同基名长 PNG 截图并放在 HTML 文件旁边；如果有短暂的窗口闪烁或者停留，属于正常情况。只在截图抓取中隐藏回向上按钮。向用户报告实际生成的文件,并在有最后回复时显示 PNG.
- 保留生成的 HTML 文件作为主输出; 从 HTML 页面中隐藏失败, 未识别, 或空源。如果截图偏好关闭,只报告 HTML; 如果截图抓取失败,仍返回 HTML 并明确声明截图失败。

## 数据来源

脚本对这些来源进行查询,顺序为:

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

## 可靠性说明

- Prefer the bundled script over rewriting fetch logic. It includes retries, timeouts, source-level error isolation, structured JSON parsing, HTML/RSS extraction, 财联社 request signing, HTML escaping, Edge/Chrome headless screenshot capture, and Pillow-based screenshot cropping when Pillow is installed.
- 如果一个源失败,被远程站点所屏蔽,不被识别,或者返回不可用项目,仍然生成HTML报告,但从页面中省略该源.
- The gold price entry is rendered as a dedicated gold price card, not as ordinary news rows. It uses spot gold data plus USDCNH conversion, then renders separate 实时 / 近一月 / 近三月 SVG trends. Keep it first so the report opens with the gold price lookup.
- For 澎湃新闻, the default node is `25950` (时事). Override it with `--paper-node-id` only when the user requests a different node.
- The Bilibili, 抖音热搜, IT之家, 少数派, 掘金, 百度热搜, 今日头条, Hacker News, 参考消息, Solidot, and 财联社 integrations were added after inspecting public source behavior. Vercel and Netlify dashboard URLs are deployment platforms, not public news-source catalogs.

## 有用选项

```bash
python <skill-dir>/scripts/generate_news_html.py --output-dir . --limit 20 --timeout 15 --retries 2
```

- `--limit`: maximum items per source, allowed `10` to `20`, default `20`.
- `--timeout`: seconds per request attempt.
- `--retries`: retries per source after the first attempt.
- `--paper-node-id`: 澎湃新闻 node id, default `25950`.
- `--print-preferences`: print the saved screenshot preference and exit without generating a report.
- `--remember-screenshot-preference yes|no`: save whether future runs should generate a PNG screenshot after the HTML report.
- `--screenshot` / `--no-screenshot`: override PNG screenshot capture for the current run.
- `--screenshot-width`, `--screenshot-height`, `--screenshot-timeout`: tune long screenshot capture. Defaults are `1200`, `60000`, and `60`.
