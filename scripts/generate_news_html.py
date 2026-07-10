#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib import error, parse, request


BEIJING_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
DEFAULT_LIMIT = 20
DEFAULT_TIMEOUT = 15
DEFAULT_RETRIES = 2
DEFAULT_SCREENSHOT_WIDTH = 1200
DEFAULT_SCREENSHOT_HEIGHT = 60000
DEFAULT_SCREENSHOT_TIMEOUT = 60
DESKTOP_VIEWPORT_WIDTH = 1200
PROJECT_GITHUB_URL = "https://github.com/guliacer/news-html-digest"
PROJECT_GITHUB_LABEL = "github.com/guliacer/news-html-digest"
SCREENSHOT_PREFERENCE_KEY = "screenshot_after_html"
PREFERENCE_PATH_ENV = "NEWS_HTML_DIGEST_PREFERENCES"


@dataclass
class NewsItem:
    title: str
    url: str = ""
    summary: str = ""
    image: str = ""
    meta: list[str] = field(default_factory=list)
    hot: str = ""


@dataclass
class SourceResult:
    name: str
    method: str
    url: str
    ok: bool
    items: list[NewsItem] = field(default_factory=list)
    error: str = ""
    extra: dict[str, str] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)


class FetchError(RuntimeError):
    pass


class ParseError(RuntimeError):
    pass


def render_project_footer() -> str:
    return (
        '<footer class="site-footer">'
        'GitHub：'
        f'<a href="{PROJECT_GITHUB_URL}" target="_blank" rel="noopener noreferrer">'
        f"{PROJECT_GITHUB_LABEL}</a>"
        '<span> · 欢迎大家使用</span>'
        "</footer>"
    )


def preference_path() -> Path:
    override = clean_text(os.environ.get(PREFERENCE_PATH_ENV))
    if override:
        return Path(override).expanduser()
    codex_home = clean_text(os.environ.get("CODEX_HOME"))
    root = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
    return root / "state" / "news-html-digest" / "preferences.json"


def read_preferences() -> dict[str, Any]:
    path = preference_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_preferences(values: dict[str, Any]) -> None:
    path = preference_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = read_preferences()
    payload.update(values)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def read_screenshot_preference() -> bool | None:
    value = read_preferences().get(SCREENSHOT_PREFERENCE_KEY)
    return value if isinstance(value, bool) else None


def print_preferences() -> None:
    payload = {
        "path": str(preference_path()),
        SCREENSHOT_PREFERENCE_KEY: read_screenshot_preference(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def clean_text(value: Any, max_length: int | None = None) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if looks_mojibake(text):
        try:
            text = text.encode("latin-1").decode("utf-8")
        except UnicodeError:
            pass
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if max_length and len(text) > max_length:
        return text[: max_length - 1].rstrip() + "..."
    return text


def looks_mojibake(text: str) -> bool:
    markers = ("Ã", "Â", "å", "æ", "ç", "è", "é", "ä", "ï¼", "ã")
    return sum(text.count(marker) for marker in markers) >= 2


def decode_response(body: bytes, headers: Any) -> str:
    content_type = headers.get("Content-Type", "") if headers else ""
    charset_match = re.search(r"charset=([\w.-]+)", content_type, re.I)
    candidates: list[str] = ["utf-8"]
    if charset_match:
        charset = charset_match.group(1).strip().lower()
        if charset not in candidates:
            candidates.insert(0, charset)
    candidates.extend(["gb18030", "latin-1"])

    seen: set[str] = set()
    for encoding in candidates:
        if encoding in seen:
            continue
        seen.add(encoding)
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def build_url(url: str, params: dict[str, Any] | None = None) -> str:
    if not params:
        return url
    query = parse.urlencode(params, doseq=True)
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{query}"


def http_request(
    url: str,
    *,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    form_body: dict[str, Any] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    headers: dict[str, str] | None = None,
) -> str:
    method = method.upper()
    request_url = build_url(url, params)
    body: bytes | None = None
    final_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "close",
    }
    if headers:
        final_headers.update(headers)

    if json_body is not None:
        body = json.dumps(json_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        final_headers.setdefault("Content-Type", "application/json;charset=UTF-8")
    elif form_body is not None:
        body = parse.urlencode(form_body, doseq=True).encode("utf-8")
        final_headers.setdefault("Content-Type", "application/x-www-form-urlencoded;charset=UTF-8")
    elif method == "POST":
        body = b""

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        req = request.Request(request_url, data=body, headers=final_headers, method=method)
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return decode_response(raw, resp.headers)
        except error.HTTPError as exc:
            raw = exc.read() if hasattr(exc, "read") else b""
            details = decode_response(raw, exc.headers)[:240].replace("\n", " ")
            last_error = FetchError(f"HTTP {exc.code}: {clean_text(details, 220)}")
        except (error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
            last_error = FetchError(str(exc))
        if attempt < retries:
            time.sleep(0.7 * (attempt + 1))
    raise FetchError(str(last_error or "request failed"))


def fetch_json(url: str, **kwargs: Any) -> Any:
    text = http_request(url, **kwargs)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        snippet = clean_text(text[:240], 220)
        raise ParseError(f"JSON parse failed: {exc.msg}; snippet={snippet}") from exc


def source_failure(name: str, method: str, url: str, exc: Exception) -> SourceResult:
    return SourceResult(name=name, method=method, url=url, ok=False, error=clean_text(str(exc), 260))


GOLD_MINI_PROGRAM_URL = "#小程序://金攒攒/CVHJpZIALFwZPbr"
GOLD_SPOT_CODE = "JO_92233"
GOLD_TROY_OUNCE_GRAMS = 31.1034768


def parse_jsonp_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ParseError("wrapped JSON object not found")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ParseError("wrapped JSON payload is not an object")
    return payload


def fetch_jsonp_object(url: str, **kwargs: Any) -> dict[str, Any]:
    return parse_jsonp_object(http_request(url, **kwargs))


def parse_jsonp_array(text: str) -> list[Any]:
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        raise ParseError("wrapped JSON array not found")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, list):
        raise ParseError("wrapped JSON payload is not a list")
    return payload


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def usd_oz_to_cny_g(value: float, fx_rate: float) -> float:
    return value * fx_rate / GOLD_TROY_OUNCE_GRAMS


def fetch_usdcnh_rate(timeout: int, retries: int) -> float:
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "secid": "133.USDCNH",
        "fields": "f43,f59,f152,f57,f58,f107,f169,f170",
        "fltt": "2",
        "invt": "2",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }
    try:
        payload = fetch_json(url, params=params, timeout=timeout, retries=max(retries, 2), headers={"Referer": "https://quote.eastmoney.com/"})
        data = payload.get("data") if isinstance(payload, dict) else {}
        rate = parse_float(data.get("f43") if isinstance(data, dict) else None)
        if rate is not None and rate > 0:
            return rate
    except Exception:
        pass

    fallback = fetch_json("https://open.er-api.com/v6/latest/USD", timeout=timeout, retries=max(retries, 2))
    rates = fallback.get("rates") if isinstance(fallback, dict) else {}
    rate = parse_float(rates.get("CNY") if isinstance(rates, dict) else None)
    if rate is None or rate <= 0:
        raise ParseError("USDCNH/CNY rate not found")
    return rate


def fetch_gold_quote(timeout: int, retries: int) -> dict[str, Any]:
    headers = {"Referer": "https://quote.cngold.org/"}
    effective_retries = max(retries, 2)
    primary_url = "https://api.jijinhao.com/quoteCenter/realTime.htm"
    try:
        payload = fetch_jsonp_object(primary_url, params={"codes": GOLD_SPOT_CODE}, timeout=timeout, retries=effective_retries, headers=headers)
        quote = payload.get(GOLD_SPOT_CODE) if isinstance(payload, dict) else {}
        if isinstance(quote, dict) and quote:
            return quote
    except Exception:
        pass

    fallback_url = "https://api.jijinhao.com/realtime/quotejs.htm"
    text = http_request(fallback_url, params={"codes": GOLD_SPOT_CODE}, timeout=timeout, retries=effective_retries, headers=headers)
    rows = parse_jsonp_array(text)
    for row in rows:
        data_rows = row.get("data") if isinstance(row, dict) else []
        if not isinstance(data_rows, list):
            continue
        for data_row in data_rows:
            quote = data_row.get("quote") if isinstance(data_row, dict) else None
            if isinstance(quote, dict) and quote:
                return quote
    raise ParseError("gold quote not found")


def format_gold_point_time(value: Any, *, include_time: bool) -> str:
    timestamp = parse_float(value)
    if timestamp is None:
        return ""
    fmt = "%m-%d %H:%M" if include_time else "%m-%d"
    return datetime.fromtimestamp(timestamp / 1000, BEIJING_TZ).strftime(fmt)


def parse_gold_history_points(rows: list[Any], fx_rate: float, *, include_time: bool) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        close_usd = parse_float(row.get("q2") or row.get("close"))
        if close_usd is None:
            continue
        label = clean_text(row.get("day")) or format_gold_point_time(row.get("time") or row.get("date"), include_time=include_time)
        points.append(
            {
                "label": label,
                "value": round(usd_oz_to_cny_g(close_usd, fx_rate), 2),
                "raw": close_usd,
            }
        )
    return points


def fetch_gold_history(style: int, page_size: int, fx_rate: float, timeout: int, retries: int) -> list[dict[str, Any]]:
    params = {"code": GOLD_SPOT_CODE, "style": style, "pageSize": page_size}
    headers = {"Referer": "https://quote.cngold.org/"}
    last_error: Exception | None = None
    for url in ("https://api.jijinhao.com/quoteCenter/history.htm", "https://api.jijinhao.com/sQuoteCenter/history.htm"):
        try:
            payload = fetch_jsonp_object(url, params=params, timeout=timeout, retries=max(retries, 2), headers=headers)
            rows = payload.get("data") if isinstance(payload, dict) else []
            if not isinstance(rows, list):
                raise ParseError("gold history data is not a list")
            return parse_gold_history_points(rows, fx_rate, include_time=(style == 1))
        except Exception as exc:
            last_error = exc
            time.sleep(0.2)
    raise ParseError(str(last_error or "gold history request failed"))


def parse_jinzanzan_gold_data(timeout: int, retries: int) -> SourceResult:
    name = "黄金金价查询"
    method = "GET"
    url = "https://api.jijinhao.com/quoteCenter/realTime.htm"
    try:
        fx_rate = fetch_usdcnh_rate(timeout, retries)
        quote = fetch_gold_quote(timeout, retries)
        price_usd = parse_float(quote.get("q5") or quote.get("q2") or quote.get("q63"))
        if price_usd is None:
            raise ParseError("gold price not found")
        change_usd = parse_float(quote.get("q70")) or 0.0
        change_percent = parse_float(quote.get("q80")) or 0.0
        current_price = round(usd_oz_to_cny_g(price_usd, fx_rate), 2)
        current_change = round(usd_oz_to_cny_g(change_usd, fx_rate), 2)
        updated = format_gold_point_time(quote.get("time"), include_time=True) or clean_text(quote.get("q59"))
        charts = [
            {
                "key": "realtime",
                "label": "实时",
                "caption": "短周期走势",
                "variant": "line",
                "points": fetch_gold_history(1, 60, fx_rate, timeout, retries),
            },
            {
                "key": "month",
                "label": "近一月",
                "caption": "31 日收盘价",
                "variant": "area",
                "points": fetch_gold_history(3, 31, fx_rate, timeout, retries),
            },
            {
                "key": "quarter",
                "label": "近三月",
                "caption": "93 日收盘价",
                "variant": "bars",
                "points": fetch_gold_history(3, 93, fx_rate, timeout, retries),
            },
        ]
        item = NewsItem(
            title=f"黄金价格 {current_price:.2f} 元/克",
            summary="现货黄金美元/盎司按美元兑离岸人民币汇率换算为人民币/克。",
            meta=[m for m in [f"更新时间 {updated}" if updated else "", f"USD/CNH {fx_rate:.4f}"] if m],
            hot=f"{current_change:+.2f} ({change_percent:+.2f}%)",
        )
        return SourceResult(
            name=name,
            method=method,
            url=build_url(url, {"codes": GOLD_SPOT_CODE}),
            ok=True,
            items=[item],
            extra={"口径": "现货黄金换算", "单位": "人民币/克"},
            data={
                "kind": "gold_price",
                "price": current_price,
                "change": current_change,
                "change_percent": change_percent,
                "price_usd": round(price_usd, 2),
                "fx_rate": fx_rate,
                "updated": updated,
                "charts": charts,
                "mini_program_url": GOLD_MINI_PROGRAM_URL,
            },
        )
    except Exception as exc:
        return source_failure(name, method, url, exc)


def parse_jinzanzan_gold_price(timeout: int = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES) -> SourceResult:
    return parse_jinzanzan_gold_data(timeout, retries)


def parse_60s(limit: int, timeout: int, retries: int) -> SourceResult:
    name = "60 秒每日要闻"
    method = "GET"
    url = "https://60s.viki.moe/v2/60s"
    try:
        payload = fetch_json(url, timeout=timeout, retries=retries)
        data = payload.get("data") if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            raise ParseError("data is not an object")
        news = data.get("news") or []
        if not isinstance(news, list):
            raise ParseError("news is not a list")
        link = clean_text(data.get("link"))
        image = clean_text(data.get("image") or data.get("cover"))
        items = [
            NewsItem(title=clean_text(title), url=link, image=image if idx == 0 else "")
            for idx, title in enumerate(news[:limit])
            if clean_text(title)
        ]
        extra = {
            "日期": clean_text(data.get("date")),
            "农历": clean_text(data.get("lunar_date")),
            "星期": clean_text(data.get("day_of_week")),
            "提示": clean_text(data.get("tip")),
            "接口更新": clean_text(data.get("api_updated") or data.get("updated")),
        }
        return SourceResult(name=name, method=method, url=url, ok=bool(items), items=items, extra=compact_dict(extra))
    except Exception as exc:
        return source_failure(name, method, url, exc)


def parse_weibo(limit: int, timeout: int, retries: int) -> SourceResult:
    name = "微博热搜"
    method = "GET"
    url = "https://60s.viki.moe/v2/weibo"
    try:
        payload = fetch_json(url, timeout=timeout, retries=retries)
        data = payload.get("data") if isinstance(payload, dict) else []
        if not isinstance(data, list):
            raise ParseError("data is not a list")
        items: list[NewsItem] = []
        for row in data[:limit]:
            if not isinstance(row, dict):
                continue
            title = clean_text(row.get("title"))
            if not title:
                continue
            hot = clean_text(row.get("hot_value") or row.get("hot_value_desc"))
            items.append(NewsItem(title=title, url=clean_text(row.get("link")), hot=hot))
        return SourceResult(name=name, method=method, url=url, ok=bool(items), items=items)
    except Exception as exc:
        return source_failure(name, method, url, exc)


def parse_zhihu(limit: int, timeout: int, retries: int) -> SourceResult:
    name = "知乎热榜"
    method = "GET"
    url = "https://60s.viki.moe/v2/zhihu"
    try:
        payload = fetch_json(url, timeout=timeout, retries=retries)
        data = payload.get("data") if isinstance(payload, dict) else []
        if not isinstance(data, list):
            raise ParseError("data is not a list")
        items: list[NewsItem] = []
        for row in data[:limit]:
            if not isinstance(row, dict):
                continue
            title = clean_text(row.get("title"))
            if not title:
                continue
            meta = []
            hot = clean_text(row.get("hot_value_desc") or row.get("hot_value"))
            answer_count = clean_text(row.get("answer_cnt"))
            follower_count = clean_text(row.get("follower_cnt"))
            if answer_count:
                meta.append(f"回答 {answer_count}")
            if follower_count:
                meta.append(f"关注 {follower_count}")
            items.append(
                NewsItem(
                    title=title,
                    url=clean_text(row.get("link")),
                    summary=clean_text(row.get("detail"), 260),
                    image=clean_text(row.get("cover")),
                    meta=meta,
                    hot=hot,
                )
            )
        return SourceResult(name=name, method=method, url=url, ok=bool(items), items=items)
    except Exception as exc:
        return source_failure(name, method, url, exc)


def parse_douyin_hot(limit: int, timeout: int, retries: int) -> SourceResult:
    name = "抖音热搜"
    method = "GET"
    url = "https://60s.viki.moe/v2/douyin"
    try:
        payload = fetch_json(url, timeout=timeout, retries=retries)
        data = payload.get("data") if isinstance(payload, dict) else []
        if not isinstance(data, list):
            raise ParseError("data is not a list")
        items: list[NewsItem] = []
        for row in data[:limit]:
            if not isinstance(row, dict):
                continue
            title = clean_text(row.get("title"))
            if not title:
                continue
            hot = clean_text(row.get("hot_value_desc") or row.get("hot_value"))
            image = clean_text(row.get("cover") or row.get("image"))
            items.append(NewsItem(title=title, url=clean_text(row.get("link")), image=image, hot=hot))
        return SourceResult(name=name, method=method, url=url, ok=bool(items), items=items)
    except Exception as exc:
        return source_failure(name, method, url, exc)


def parse_tencent(limit: int, timeout: int, retries: int) -> SourceResult:
    name = "腾讯新闻科技"
    method = "POST"
    url = "https://i.news.qq.com/web_feed/getPCList"
    device_id = "123456789012345678901234"
    params = {
        "base_req[from]": "pc",
        "forward": "2",
        "channel_id": "news_news_tech",
        "flush_num": "1",
        "item_count": str(limit),
        "qimei36": device_id,
        "device_id": device_id,
    }
    try:
        payload = fetch_json(
            url,
            method=method,
            params=params,
            timeout=timeout,
            retries=retries,
            headers={"Referer": "https://news.qq.com/ch/tech"},
        )
        if not isinstance(payload, dict):
            raise ParseError("response is not an object")
        if payload.get("code") not in (0, "0", None):
            raise ParseError(clean_text(payload.get("message") or payload.get("msg") or payload.get("code")))
        data = payload.get("data") or []
        if not isinstance(data, list):
            raise ParseError("data is not a list")
        items: list[NewsItem] = []
        seen: set[str] = set()
        for row in flatten_tencent(data):
            title = clean_text(row.get("title") or row.get("short_title"))
            if not title or title == "热点精选" or title in seen:
                continue
            seen.add(title)
            link_info = row.get("link_info") if isinstance(row.get("link_info"), dict) else {}
            media_info = row.get("media_info") if isinstance(row.get("media_info"), dict) else {}
            meta = [clean_text(row.get("publish_time")), clean_text(media_info.get("chl_name"))]
            items.append(
                NewsItem(
                    title=title,
                    url=clean_text(link_info.get("share_url") or link_info.get("url") or link_info.get("short_url")),
                    summary=clean_text(row.get("desc") or row.get("long_summary"), 280),
                    image=first_tencent_image(row),
                    meta=[m for m in meta if m],
                )
            )
            if len(items) >= limit:
                break
        if not items:
            raise ParseError("no usable Tencent news items found")
        return SourceResult(name=name, method=method, url=url, ok=True, items=items)
    except Exception as exc:
        return source_failure(name, method, url, exc)


def flatten_tencent(rows: list[Any]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sub_items = row.get("sub_item")
        if isinstance(sub_items, list):
            flattened.extend([item for item in sub_items if isinstance(item, dict)])
        if row.get("title") != "热点精选":
            flattened.append(row)
    return flattened


def first_tencent_image(row: dict[str, Any]) -> str:
    pic_info = row.get("pic_info") if isinstance(row.get("pic_info"), dict) else {}
    for key in ("share_img", "big_img", "small_img", "three_img"):
        value = pic_info.get(key)
        if isinstance(value, str) and value:
            return clean_text(value)
        if isinstance(value, list) and value:
            return clean_text(value[0])
    return ""


def parse_thepaper(limit: int, timeout: int, retries: int, paper_node_id: str) -> SourceResult:
    name = "澎湃新闻"
    method = "POST"
    url = "https://api.thepaper.cn/contentapi/nodeCont/getByNodeIdPortal"
    try:
        payload = fetch_json(
            url,
            method=method,
            json_body={"nodeId": str(paper_node_id), "pageNum": 1, "pageSize": limit},
            timeout=timeout,
            retries=retries,
            headers={"Referer": "https://www.thepaper.cn/"},
        )
        if not isinstance(payload, dict):
            raise ParseError("response is not an object")
        if payload.get("code") not in (200, "200"):
            raise ParseError(clean_text(payload.get("desc") or payload.get("message") or payload.get("code")))
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        rows = data.get("list") or []
        if not isinstance(rows, list):
            raise ParseError("data.list is not a list")
        items: list[NewsItem] = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            title = clean_text(row.get("name") or row.get("title"))
            if not title:
                continue
            cont_id = clean_text(row.get("contId") or row.get("originalContId"))
            link = clean_text(row.get("link"))
            if not link and cont_id:
                link = f"https://www.thepaper.cn/newsDetail_forward_{cont_id}"
            node_info = row.get("nodeInfo") if isinstance(row.get("nodeInfo"), dict) else {}
            meta = [clean_text(row.get("pubTimeNew") or row.get("pubTime")), clean_text(node_info.get("name"))]
            items.append(
                NewsItem(
                    title=title,
                    url=link,
                    image=clean_text(row.get("smallPic") or row.get("pic") or row.get("sharePic")),
                    meta=[m for m in meta if m],
                    hot=clean_text(row.get("cornerLabelDesc")),
                )
            )
        node_info = data.get("nodeInfo") if isinstance(data.get("nodeInfo"), dict) else {}
        extra = {"节点": clean_text(node_info.get("name")), "nodeId": str(paper_node_id)}
        return SourceResult(name=name, method=method, url=url, ok=bool(items), items=items, extra=compact_dict(extra))
    except Exception as exc:
        return source_failure(name, method, url, exc)


def parse_eastmoney_stock_news(limit: int, timeout: int, retries: int) -> SourceResult:
    name = "东方财富股票新闻"
    method = "GET"
    url = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"
    params = {
        "client": "web",
        "biz": "web_news_col",
        "column": "354",
        "order": "1",
        "needInteractData": "0",
        "page_index": "1",
        "page_size": limit,
        "req_trace": int(time.time() * 1000),
        "fields": "code,showTime,title,mediaName,summary,image,url,uniqueUrl,Np_dst",
        "types": "1,20",
    }
    try:
        payload = fetch_json(
            url,
            params=params,
            timeout=timeout,
            retries=retries,
            headers={"Referer": "https://finance.eastmoney.com/a/cgsxw.html"},
        )
        if not isinstance(payload, dict):
            raise ParseError("response is not an object")
        if payload.get("code") not in (1, "1", 0, "0", None):
            raise ParseError(clean_text(payload.get("message") or payload.get("msg") or payload.get("code")))
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        rows = data.get("list") if isinstance(data, dict) else []
        if not isinstance(rows, list):
            raise ParseError("data.list is not a list")
        items: list[NewsItem] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = clean_text(row.get("title"))
            if not title or title in seen:
                continue
            seen.add(title)
            code = clean_text(row.get("code"))
            link = clean_text(row.get("uniqueUrl") or row.get("url"))
            if not link and code:
                link = f"https://finance.eastmoney.com/a/{code}.html"
            items.append(
                NewsItem(
                    title=title,
                    url=link,
                    summary=clean_text(row.get("summary"), 260),
                    image=clean_text(row.get("image")),
                    meta=[m for m in [clean_text(row.get("showTime")), clean_text(row.get("mediaName"))] if m],
                )
            )
            if len(items) >= limit:
                break
        extra = {"栏目": "公司资讯", "column": "354"}
        return SourceResult(name=name, method=method, url=build_url(url, params), ok=bool(items), items=items, extra=extra)
    except Exception as exc:
        return source_failure(name, method, url, exc)


class ItHomeListParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, str]] = []
        self._current: dict[str, Any] | None = None
        self._li_depth = 0
        self._capture: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        tag = tag.lower()
        if tag == "li":
            if self._current is None:
                self._current = {"href": "", "title": [], "date": []}
                self._li_depth = 1
            else:
                self._li_depth += 1
        if self._current is None:
            return
        classes = attrs_dict.get("class", "").split()
        if tag == "a" and "t" in classes:
            self._current["href"] = attrs_dict.get("href", "")
            self._capture = "title"
        elif tag == "i":
            self._capture = "date"

    def handle_data(self, data: str) -> None:
        if self._current is not None and self._capture:
            self._current[self._capture].append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "a" and self._capture == "title":
            self._capture = None
        elif tag == "i" and self._capture == "date":
            self._capture = None
        elif tag == "li" and self._current is not None:
            self._li_depth -= 1
            if self._li_depth <= 0:
                href = clean_text(self._current.get("href"))
                title = clean_text("".join(self._current.get("title") or []))
                date = clean_text("".join(self._current.get("date") or []))
                if href and title and date:
                    self.items.append({"href": href, "title": title, "date": date})
                self._current = None
                self._capture = None
                self._li_depth = 0


class HackerNewsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, str]] = []
        self.scores: dict[str, str] = {}
        self._current: dict[str, Any] | None = None
        self._in_titleline = False
        self._capture_title = False
        self._score_id = ""
        self._score_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        tag = tag.lower()
        classes = attrs_dict.get("class", "").split()
        if tag == "tr" and "athing" in classes:
            self._current = {"id": attrs_dict.get("id", ""), "href": "", "title": []}
        elif self._current is not None and tag == "span" and "titleline" in classes:
            self._in_titleline = True
        elif self._current is not None and self._in_titleline and tag == "a" and not self._current.get("href"):
            self._current["href"] = attrs_dict.get("href", "")
            self._capture_title = True
        elif tag == "span" and attrs_dict.get("id", "").startswith("score_"):
            self._score_id = attrs_dict.get("id", "").removeprefix("score_")
            self._score_chunks = []

    def handle_data(self, data: str) -> None:
        if self._capture_title and self._current is not None:
            self._current["title"].append(data)
        if self._score_id:
            self._score_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "a" and self._capture_title:
            self._capture_title = False
        elif tag == "span" and self._score_id:
            self.scores[self._score_id] = clean_text("".join(self._score_chunks))
            self._score_id = ""
            self._score_chunks = []
        elif tag == "span" and self._in_titleline:
            self._in_titleline = False
        elif tag == "tr" and self._current is not None:
            item_id = clean_text(self._current.get("id"))
            title = clean_text("".join(self._current.get("title") or []))
            href = clean_text(self._current.get("href"))
            if item_id and title:
                self.items.append({"id": item_id, "title": title, "href": href})
            self._current = None
            self._in_titleline = False
            self._capture_title = False


class HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self.chunks.append(data)

    def text(self) -> str:
        return clean_text(" ".join(self.chunks))


def strip_html(value: Any, max_length: int | None = None) -> str:
    text = clean_text(value)
    if not text:
        return ""
    parser = HTMLTextExtractor()
    parser.feed(text)
    return clean_text(parser.text(), max_length)


def format_number(value: Any) -> str:
    try:
        number = int(float(str(value)))
    except (TypeError, ValueError):
        return clean_text(value)
    if number >= 100000000:
        return f"{number / 100000000:.1f}亿".rstrip("0").rstrip(".")
    if number >= 10000:
        return f"{number / 10000:.1f}万".rstrip("0").rstrip(".")
    return str(number)


def format_unix_time(value: Any) -> str:
    try:
        timestamp = float(str(value))
    except (TypeError, ValueError):
        return clean_text(value)
    if timestamp > 100000000000:
        timestamp /= 1000
    return datetime.fromtimestamp(timestamp, BEIJING_TZ).strftime("%Y-%m-%d %H:%M")


def xml_child_text(element: ET.Element, *names: str) -> str:
    wanted = set(names)
    for child in list(element):
        local_name = child.tag.rsplit("}", 1)[-1]
        if local_name in wanted:
            return clean_text("".join(child.itertext()))
    return ""


def xml_child_attr(element: ET.Element, name: str, attr: str) -> str:
    for child in list(element):
        local_name = child.tag.rsplit("}", 1)[-1]
        if local_name == name:
            return clean_text(child.attrib.get(attr))
    return ""


def parse_rss_items(text: str, limit: int) -> list[NewsItem]:
    root = ET.fromstring(text)
    root_name = root.tag.rsplit("}", 1)[-1]
    if root_name == "rss":
        channel = next((child for child in list(root) if child.tag.rsplit("}", 1)[-1] == "channel"), root)
        entries = [child for child in list(channel) if child.tag.rsplit("}", 1)[-1] == "item"]
    else:
        entries = [child for child in list(root) if child.tag.rsplit("}", 1)[-1] == "entry"]

    items: list[NewsItem] = []
    for entry in entries[:limit]:
        title = xml_child_text(entry, "title")
        if not title:
            continue
        link = xml_child_text(entry, "link") or xml_child_attr(entry, "link", "href")
        summary = strip_html(xml_child_text(entry, "description", "summary", "content"), 220)
        date = xml_child_text(entry, "pubDate", "published", "updated")
        meta = [date] if date else []
        items.append(NewsItem(title=title, url=link, summary=summary, meta=meta))
    return items


def cls_signed_params(extra: dict[str, Any] | None = None) -> dict[str, str]:
    params = {"appName": "CailianpressWeb", "os": "web", "sv": "7.7.5"}
    if extra:
        params.update({key: str(value) for key, value in extra.items()})
    query = parse.urlencode(sorted(params.items()))
    sign = hashlib.md5(hashlib.sha1(query.encode("utf-8")).hexdigest().encode("utf-8")).hexdigest()
    params["sign"] = sign
    return params


def parse_bilibili_hot_search(limit: int, timeout: int, retries: int) -> SourceResult:
    name = "哔哩哔哩热搜"
    method = "GET"
    url = "https://s.search.bilibili.com/main/hotword"
    try:
        payload = fetch_json(url, params={"limit": limit}, timeout=timeout, retries=retries)
        rows = payload.get("list") if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            raise ParseError("list is not a list")
        items: list[NewsItem] = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            keyword = clean_text(row.get("keyword"))
            title = clean_text(row.get("show_name") or keyword)
            if not title:
                continue
            hot = clean_text(row.get("heat_layer") or row.get("score") or row.get("heat_score"))
            items.append(
                NewsItem(
                    title=title,
                    url=f"https://search.bilibili.com/all?keyword={parse.quote(keyword or title)}",
                    image=clean_text(row.get("icon")),
                    hot=hot,
                )
            )
        return SourceResult(name=name, method=method, url=build_url(url, {"limit": limit}), ok=bool(items), items=items)
    except Exception as exc:
        return source_failure(name, method, url, exc)


def parse_bilibili_popular(limit: int, timeout: int, retries: int) -> SourceResult:
    name = "哔哩哔哩热门视频"
    method = "GET"
    url = "https://api.bilibili.com/x/web-interface/popular"
    try:
        payload = fetch_json(url, timeout=timeout, retries=retries)
        data = payload.get("data") if isinstance(payload, dict) else {}
        rows = data.get("list") if isinstance(data, dict) else []
        if not isinstance(rows, list):
            raise ParseError("data.list is not a list")
        items: list[NewsItem] = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            title = clean_text(row.get("title"))
            bvid = clean_text(row.get("bvid"))
            if not title or not bvid:
                continue
            owner = row.get("owner") if isinstance(row.get("owner"), dict) else {}
            stat = row.get("stat") if isinstance(row.get("stat"), dict) else {}
            meta = [
                clean_text(owner.get("name")),
                f"{format_number(stat.get('view'))}观看" if stat.get("view") is not None else "",
                f"{format_number(stat.get('like'))}点赞" if stat.get("like") is not None else "",
            ]
            items.append(
                NewsItem(
                    title=title,
                    url=f"https://www.bilibili.com/video/{bvid}",
                    summary=clean_text(row.get("desc"), 220),
                    image=clean_text(row.get("pic")),
                    meta=[m for m in meta if m],
                )
            )
        return SourceResult(name=name, method=method, url=url, ok=bool(items), items=items)
    except Exception as exc:
        return source_failure(name, method, url, exc)


def parse_ithome(limit: int, timeout: int, retries: int) -> SourceResult:
    name = "IT之家"
    method = "GET"
    url = "https://www.ithome.com/list/"
    try:
        text = http_request(url, timeout=timeout, retries=retries, headers={"Referer": "https://www.ithome.com/"})
        parser = ItHomeListParser()
        parser.feed(text)
        items: list[NewsItem] = []
        seen: set[str] = set()
        ad_keywords = ("神券", "优惠", "补贴", "京东")
        for row in parser.items:
            title = clean_text(row.get("title"))
            href = clean_text(row.get("href"))
            if not title or not href or title in seen:
                continue
            if "lapin" in href or any(keyword in title for keyword in ad_keywords):
                continue
            seen.add(title)
            items.append(NewsItem(title=title, url=parse.urljoin(url, href), meta=[clean_text(row.get("date"))]))
            if len(items) >= limit:
                break
        if not items:
            raise ParseError("no usable IT Home items found")
        return SourceResult(name=name, method=method, url=url, ok=True, items=items)
    except Exception as exc:
        return source_failure(name, method, url, exc)


def parse_sspai(limit: int, timeout: int, retries: int) -> SourceResult:
    name = "少数派热门"
    method = "GET"
    url = "https://sspai.com/api/v1/article/tag/page/get"
    params = {
        "limit": limit,
        "offset": 0,
        "created_at": int(time.time() * 1000),
        "tag": "热门文章",
        "released": "false",
    }
    try:
        payload = fetch_json(url, params=params, timeout=timeout, retries=retries, headers={"Referer": "https://sspai.com/"})
        rows = payload.get("data") if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            raise ParseError("data is not a list")
        items = [
            NewsItem(title=clean_text(row.get("title")), url=f"https://sspai.com/post/{clean_text(row.get('id'))}")
            for row in rows[:limit]
            if isinstance(row, dict) and clean_text(row.get("title")) and clean_text(row.get("id"))
        ]
        return SourceResult(name=name, method=method, url=build_url(url, params), ok=bool(items), items=items)
    except Exception as exc:
        return source_failure(name, method, url, exc)


def parse_juejin(limit: int, timeout: int, retries: int) -> SourceResult:
    name = "稀土掘金热榜"
    method = "GET"
    url = "https://api.juejin.cn/content_api/v1/content/article_rank"
    params = {"category_id": "1", "type": "hot", "spider": "0"}
    try:
        payload = fetch_json(url, params=params, timeout=timeout, retries=retries, headers={"Referer": "https://juejin.cn/"})
        rows = payload.get("data") if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            raise ParseError("data is not a list")
        items: list[NewsItem] = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            content = row.get("content") if isinstance(row.get("content"), dict) else {}
            title = clean_text(content.get("title"))
            content_id = clean_text(content.get("content_id"))
            if title and content_id:
                items.append(NewsItem(title=title, url=f"https://juejin.cn/post/{content_id}"))
        return SourceResult(name=name, method=method, url=build_url(url, params), ok=bool(items), items=items)
    except Exception as exc:
        return source_failure(name, method, url, exc)


def parse_baidu_hot(limit: int, timeout: int, retries: int) -> SourceResult:
    name = "百度热搜"
    method = "GET"
    url = "https://top.baidu.com/board?tab=realtime"
    try:
        text = http_request(url, timeout=timeout, retries=retries, headers={"Referer": "https://top.baidu.com/"})
        match = re.search(r"<!--s-data:(.*?)-->", text, re.S)
        if not match:
            raise ParseError("embedded s-data JSON not found")
        payload = json.loads(match.group(1))
        data = payload.get("data") if isinstance(payload, dict) else {}
        cards = data.get("cards") if isinstance(data, dict) else []
        rows = cards[0].get("content") if cards and isinstance(cards[0], dict) else []
        if not isinstance(rows, list):
            raise ParseError("cards[0].content is not a list")
        items: list[NewsItem] = []
        for row in rows:
            if not isinstance(row, dict) or row.get("isTop"):
                continue
            title = clean_text(row.get("word"))
            if title:
                items.append(
                    NewsItem(
                        title=title,
                        url=clean_text(row.get("rawUrl")),
                        summary=clean_text(row.get("desc"), 220),
                        hot=clean_text(row.get("hotScore") or row.get("hotTag")),
                    )
                )
            if len(items) >= limit:
                break
        return SourceResult(name=name, method=method, url=url, ok=bool(items), items=items)
    except Exception as exc:
        return source_failure(name, method, url, exc)


def parse_toutiao_hot(limit: int, timeout: int, retries: int) -> SourceResult:
    name = "今日头条热榜"
    method = "GET"
    url = "https://www.toutiao.com/hot-event/hot-board/"
    params = {"origin": "toutiao_pc"}
    try:
        payload = fetch_json(url, params=params, timeout=timeout, retries=retries, headers={"Referer": "https://www.toutiao.com/"})
        rows = payload.get("data") if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            raise ParseError("data is not a list")
        items: list[NewsItem] = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            title = clean_text(row.get("Title"))
            cluster_id = clean_text(row.get("ClusterIdStr"))
            image_info = row.get("Image") if isinstance(row.get("Image"), dict) else {}
            if title and cluster_id:
                items.append(
                    NewsItem(
                        title=title,
                        url=f"https://www.toutiao.com/trending/{cluster_id}/",
                        image=clean_text(image_info.get("url")),
                        hot=clean_text(row.get("HotValue")),
                    )
                )
        return SourceResult(name=name, method=method, url=build_url(url, params), ok=bool(items), items=items)
    except Exception as exc:
        return source_failure(name, method, url, exc)


def parse_hackernews(limit: int, timeout: int, retries: int) -> SourceResult:
    name = "Hacker News"
    method = "GET"
    url = "https://news.ycombinator.com/"
    try:
        text = http_request(url, timeout=timeout, retries=retries)
        parser = HackerNewsParser()
        parser.feed(text)
        items: list[NewsItem] = []
        for row in parser.items[:limit]:
            item_id = clean_text(row.get("id"))
            title = clean_text(row.get("title"))
            href = clean_text(row.get("href"))
            if title and item_id:
                meta = [parser.scores.get(item_id, "")] if parser.scores.get(item_id) else []
                items.append(NewsItem(title=title, url=parse.urljoin(url, href or f"item?id={item_id}"), meta=meta))
        if not items:
            raise ParseError("no Hacker News items found")
        return SourceResult(name=name, method=method, url=url, ok=True, items=items)
    except Exception as exc:
        return source_failure(name, method, url, exc)


def parse_cankaoxiaoxi(limit: int, timeout: int, retries: int) -> SourceResult:
    name = "参考消息"
    method = "GET"
    channels = ["zhongguo", "guandian", "gj"]
    url = "http://china.cankaoxiaoxi.com/json/channel/{zhongguo,guandian,gj}/list.json"
    try:
        items: list[NewsItem] = []
        seen: set[str] = set()
        for channel in channels:
            channel_url = f"http://china.cankaoxiaoxi.com/json/channel/{channel}/list.json"
            payload = fetch_json(channel_url, timeout=timeout, retries=retries, headers={"Referer": "https://china.cankaoxiaoxi.com/"})
            rows = payload.get("list") if isinstance(payload, dict) else []
            if not isinstance(rows, list):
                continue
            for row in rows:
                data = row.get("data") if isinstance(row, dict) and isinstance(row.get("data"), dict) else {}
                title = clean_text(data.get("title"))
                link = clean_text(data.get("url"))
                if not title or title in seen:
                    continue
                seen.add(title)
                items.append(NewsItem(title=title, url=link, meta=[clean_text(data.get("publishTime"))]))
        items = sorted(items, key=lambda item: item.meta[0] if item.meta else "", reverse=True)[:limit]
        return SourceResult(name=name, method=method, url=url, ok=bool(items), items=items, extra={"频道": "中国 / 观点 / 国际"})
    except Exception as exc:
        return source_failure(name, method, url, exc)


def parse_solidot(limit: int, timeout: int, retries: int) -> SourceResult:
    name = "Solidot"
    method = "GET"
    url = "https://www.solidot.org/index.rss"
    try:
        text = http_request(url, timeout=timeout, retries=retries, headers={"Referer": "https://www.solidot.org/"})
        items = parse_rss_items(text, limit)
        return SourceResult(name=name, method=method, url=url, ok=bool(items), items=items)
    except Exception as exc:
        return source_failure(name, method, url, exc)


def parse_cls_hot(limit: int, timeout: int, retries: int) -> SourceResult:
    name = "财联社热榜"
    method = "GET"
    url = "https://www.cls.cn/v2/article/hot/list"
    try:
        payload = fetch_json(url, params=cls_signed_params(), timeout=timeout, retries=retries, headers={"Referer": "https://www.cls.cn/"})
        rows = payload.get("data") if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            raise ParseError("data is not a list")
        items: list[NewsItem] = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            title = clean_text(row.get("title") or row.get("brief"))
            item_id = clean_text(row.get("id"))
            if title and item_id:
                items.append(
                    NewsItem(
                        title=title,
                        url=f"https://www.cls.cn/detail/{item_id}",
                        summary=clean_text(row.get("brief"), 220) if row.get("title") else "",
                    )
                )
        return SourceResult(name=name, method=method, url=url, ok=bool(items), items=items)
    except Exception as exc:
        return source_failure(name, method, url, exc)


def parse_cls_telegraph(limit: int, timeout: int, retries: int) -> SourceResult:
    name = "财联社电报"
    method = "GET"
    url = "https://www.cls.cn/v1/roll/get_roll_list"
    params = cls_signed_params({"last_time": int(time.time()), "refresh_type": 1, "rn": limit})
    try:
        payload = fetch_json(url, params=params, timeout=timeout, retries=retries, headers={"Referer": "https://www.cls.cn/telegraph"})
        data = payload.get("data") if isinstance(payload, dict) else {}
        rows = data.get("roll_data") if isinstance(data, dict) else []
        if not isinstance(rows, list):
            raise ParseError("data.roll_data is not a list")
        items: list[NewsItem] = []
        for row in rows:
            if not isinstance(row, dict) or row.get("is_ad"):
                continue
            title = clean_text(row.get("title") or row.get("brief"))
            item_id = clean_text(row.get("id"))
            if title and item_id:
                items.append(
                    NewsItem(
                        title=title,
                        url=f"https://www.cls.cn/detail/{item_id}",
                        summary=clean_text(row.get("brief"), 260) if row.get("title") else "",
                        meta=[format_unix_time(row.get("ctime"))],
                    )
                )
            if len(items) >= limit:
                break
        return SourceResult(name=name, method=method, url=url, ok=bool(items), items=items)
    except Exception as exc:
        return source_failure(name, method, url, exc)


def compact_dict(values: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in values.items() if value}


def render_html(results: list[SourceResult], generated_at: datetime) -> str:
    ok_count = sum(1 for result in results if result.ok)
    total_items = sum(len(result.items) for result in results)
    generated_display = generated_at.strftime("%Y-%m-%d %H:%M:%S UTC+8")
    sections = "\n".join(render_source(result) for result in results)
    summary_chips = "\n".join(
        f'<span class="chip {"ok" if result.ok else "fail"}">{escape(result.name)} · {"成功" if result.ok else "失败"} · {len(result.items)}</span>'
        for result in results
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width={DESKTOP_VIEWPORT_WIDTH}">
  <title>新闻日报 - {escape(generated_display)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f8fb;
      --panel: #ffffff;
      --text: #162033;
      --muted: #667085;
      --line: #d9e0ea;
      --accent: #0f766e;
      --accent-soft: #e6f4f1;
      --warn: #b42318;
      --warn-soft: #fff1f0;
      --shadow: 0 12px 30px rgba(22, 32, 51, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      line-height: 1.6;
      min-width: 1220px;
    }}
    .page {{
      width: 1180px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}
    header {{
      margin-bottom: 22px;
      border-bottom: 1px solid var(--line);
      padding-bottom: 20px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 34px;
      line-height: 1.2;
      letter-spacing: 0;
    }}
    .subhead {{
      color: var(--muted);
      margin: 0;
      font-size: 15px;
    }}
    .summary {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 18px;
    }}
    .chip {{
      display: inline-flex;
      align-items: center;
      min-height: 30px;
      border-radius: 6px;
      padding: 4px 9px;
      font-size: 13px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--muted);
    }}
    .chip.ok {{ background: var(--accent-soft); color: var(--accent); border-color: #b7ded8; }}
    .chip.fail {{ background: var(--warn-soft); color: var(--warn); border-color: #ffd0cb; }}
    .source {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      margin: 18px 0;
      overflow: hidden;
    }}
    .source-head {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 14px;
      padding: 18px 20px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfe;
    }}
    .source h2 {{
      margin: 0 0 6px;
      font-size: 21px;
      letter-spacing: 0;
    }}
    .source-meta {{
      color: var(--muted);
      font-size: 13px;
      word-break: break-all;
    }}
    .badge {{
      align-self: start;
      border-radius: 6px;
      padding: 5px 9px;
      font-size: 13px;
      font-weight: 650;
    }}
    .badge.ok {{ background: var(--accent-soft); color: var(--accent); }}
    .badge.fail {{ background: var(--warn-soft); color: var(--warn); }}
    .extras {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      padding: 12px 20px 0;
    }}
    .extra {{
      color: var(--muted);
      background: #f2f5f9;
      border-radius: 6px;
      padding: 4px 8px;
      font-size: 13px;
    }}
    ol.items {{
      list-style: none;
      counter-reset: item;
      margin: 0;
      padding: 8px 20px 18px;
    }}
    .item {{
      counter-increment: item;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 16px;
      padding: 14px 0;
      border-bottom: 1px solid #eef2f6;
    }}
    .item:last-child {{ border-bottom: 0; }}
    .item-main {{ min-width: 0; }}
    .title-row {{
      display: grid;
      grid-template-columns: 34px minmax(0, 1fr);
      gap: 8px;
      align-items: start;
    }}
    .title-row::before {{
      content: counter(item);
      display: inline-flex;
      justify-content: center;
      align-items: center;
      width: 28px;
      height: 28px;
      border-radius: 6px;
      background: #edf3f8;
      color: #344054;
      font-size: 13px;
      font-weight: 700;
    }}
    a {{ color: #155eef; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .item-title {{
      font-size: 16px;
      font-weight: 700;
      color: var(--text);
      overflow-wrap: anywhere;
    }}
    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin: 8px 0 0 42px;
    }}
    .meta span {{
      border-radius: 6px;
      background: #f2f4f7;
      color: #667085;
      padding: 2px 7px;
      font-size: 12px;
    }}
    .thumb {{
      width: 132px;
      aspect-ratio: 4 / 3;
      object-fit: cover;
      border-radius: 6px;
      border: 1px solid var(--line);
      background: #edf2f7;
    }}
    .error {{
      color: var(--warn);
      background: var(--warn-soft);
      margin: 16px 20px 20px;
      padding: 12px;
      border-radius: 6px;
      border: 1px solid #ffd0cb;
      overflow-wrap: anywhere;
    }}
    .site-footer {{
      color: var(--muted);
      font-size: 13px;
      margin-top: 24px;
      text-align: center;
    }}
    .site-footer a {{
      color: inherit;
      text-decoration: none;
      border-bottom: 1px solid rgba(102, 112, 133, 0.28);
    }}
    .site-footer a:hover,
    .site-footer a:focus-visible {{
      color: var(--accent);
      border-bottom-color: currentColor;
      outline: none;
    }}
    .back-to-top {{
      position: fixed;
      right: max(24px, calc((100vw - 1180px) / 2 - 58px));
      bottom: 18px;
      z-index: 20;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 42px;
      height: 42px;
      border-radius: 999px;
      border: 1px solid var(--border-strong);
      background: rgba(255, 255, 255, 0.92);
      color: var(--accent);
      box-shadow: var(--shadow);
      font-size: 20px;
      font-weight: 900;
      text-decoration: none;
    }}
    .back-to-top:hover,
    .back-to-top:focus-visible {{
      border-color: var(--accent);
      outline: none;
    }}
  </style>
</head>
<body>
  <main id="top" class="page">
    <header>
      <h1>新闻日报</h1>
      <p class="subhead">生成时间：{escape(generated_display)} · 成功来源 {ok_count}/{len(results)} · 收录 {total_items} 条</p>
      <div class="summary">
        {summary_chips}
      </div>
    </header>
    {sections}
    {render_project_footer()}
  </main>
  <a class="back-to-top" href="#top" aria-label="回到顶部" title="回到顶部">↑</a>
</body>
</html>
"""


def render_source(result: SourceResult) -> str:
    extra_html = ""
    if result.extra:
        extra_html = '<div class="extras">' + "".join(
            f'<span class="extra">{escape(key)}：{escape(value)}</span>'
            for key, value in result.extra.items()
        ) + "</div>"
    if result.ok:
        body = f"<ol class=\"items\">{''.join(render_item(item) for item in result.items)}</ol>"
    else:
        body = f'<div class="error">{escape(result.error or "未能获取该来源数据")}</div>'
    return f"""
    <section class="source">
      <div class="source-head">
        <div>
          <h2>{escape(result.name)}</h2>
          <div class="source-meta">{escape(result.method)} · {escape(result.url)}</div>
        </div>
      </div>
      {extra_html}
      {body}
    </section>
"""


def render_item(item: NewsItem) -> str:
    title = escape(item.title)
    if item.url:
        title_html = f'<a class="item-title" href="{escape(item.url, quote=True)}" target="_blank" rel="noopener noreferrer">{title}</a>'
    else:
        title_html = f'<span class="item-title">{title}</span>'
    meta_values = [value for value in [item.hot, *item.meta] if value]
    meta_html = ""
    if meta_values:
        meta_html = '<div class="meta">' + "".join(f"<span>{escape(value)}</span>" for value in meta_values) + "</div>"
    image_html = ""
    if item.image:
        image_html = f'<img class="thumb" src="{escape(item.image, quote=True)}" alt="" loading="lazy">'
    return f"""
        <li class="item">
          <div class="item-main">
            <div class="title-row">{title_html}</div>
            {meta_html}
          </div>
          {image_html}
        </li>
"""


SOURCE_THEME_CLASSES = [
    "theme-blue",
    "theme-orange",
    "theme-violet",
    "theme-green",
    "theme-red",
    "theme-teal",
]

FORCED_COMPACT_SOURCE_NAMES = {
    "60 秒每日要闻",
    "IT之家",
    "稀土掘金热榜",
    "百度热搜",
    "Hacker News",
    "Solidot",
    "抖音热搜",
    "财联社热榜",
    "财联社电报",
}


def is_compact_source(result: SourceResult) -> bool:
    if not result.items:
        return False
    if result.name in FORCED_COMPACT_SOURCE_NAMES:
        return True
    item_count = len(result.items)
    title_lengths = [len(item.title) for item in result.items if item.title]
    if not title_lengths:
        return False
    summary_lengths = [len(item.summary) for item in result.items if item.summary]
    image_count = sum(1 for item in result.items if item.image)
    avg_title_length = sum(title_lengths) / len(title_lengths)
    max_title_length = max(title_lengths)
    summary_count = len(summary_lengths)

    short_headline_list = summary_count == 0 and avg_title_length <= 26 and max_title_length <= 44
    small_brief_card = item_count <= 6 and summary_count <= 1 and avg_title_length <= 34 and max_title_length <= 52
    image_heavy_with_long_titles = image_count >= item_count // 2 and max_title_length > 36
    return (short_headline_list or small_brief_card) and not image_heavy_with_long_titles


def render_news_sections(results: list[SourceResult]) -> str:
    return "\n".join(render_news_source_cards(result, index) for index, result in enumerate(results))


def source_anchor(index: int) -> str:
    return f"source-{index + 1}"


def render_news_card_html(results: list[SourceResult], generated_at: datetime) -> str:
    visible_results = [result for result in results if result.ok and result.items]
    visible_count = len(visible_results)
    total_items = sum(len(result.items) for result in visible_results)
    generated_display = generated_at.strftime("%Y-%m-%d %H:%M:%S UTC+8")
    generated_time = generated_at.strftime("%H:%M:%S")
    sections = render_news_sections(visible_results)
    section_grid = f'<div class="source-grid">\n      {sections}\n    </div>' if sections else render_empty_state()
    source_chips = "\n".join(render_source_chip(result, index) for index, result in enumerate(visible_results))
    source_overview = f'<div class="source-overview">\n        {source_chips}\n      </div>' if source_chips else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width={DESKTOP_VIEWPORT_WIDTH}">
  <title>新闻日报 - {escape(generated_display)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f7fb;
      --card: #ffffff;
      --card-inner: #f8fbff;
      --text: #111827;
      --muted: #657083;
      --border: #e3e9f2;
      --border-strong: #c9d5e4;
      --blue: #3478f6;
      --blue-deep: #1f3f78;
      --surface-blue: #eff6ff;
      --green: #13a66b;
      --green-deep: #087a5b;
      --surface-green: #ecfff7;
      --orange: #f47b20;
      --orange-deep: #914b08;
      --surface-orange: #fff6ea;
      --red: #e34b5f;
      --red-deep: #a21d35;
      --surface-red: #fff1f3;
      --violet: #6657f5;
      --violet-deep: #3f32b8;
      --surface-violet: #f2f0ff;
      --teal: #0f766e;
      --teal-deep: #115e59;
      --surface-teal: #e8faf7;
      --shadow: 0 14px 34px rgba(17, 24, 39, 0.07);
      --page-max: 1120px;
    }}

    * {{ box-sizing: border-box; }}

    body {{
      margin: 0;
      background: linear-gradient(180deg, #f8fbff 0%, var(--bg) 48%, #edf3fb 100%);
      color: var(--text);
      font-family: "Noto Sans SC", "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
      line-height: 1.6;
      min-width: {DESKTOP_VIEWPORT_WIDTH}px;
    }}

    .page {{
      width: var(--page-max);
      margin: 0 auto;
      padding: 28px 0 56px;
    }}

    .report-head {{
      background: var(--card);
      border: 1px solid var(--border-strong);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 24px;
      margin-bottom: 18px;
    }}

    .title-line {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 18px;
      align-items: start;
      border-bottom: 1px solid var(--border);
      padding-bottom: 18px;
    }}

    h1 {{
      margin: 0;
      font-size: 34px;
      line-height: 1.18;
      letter-spacing: 0;
      color: var(--text);
      text-wrap: balance;
    }}

    .subhead {{
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 15px;
    }}

    .metric-strip {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-top: 16px;
    }}

    .metric-tile {{
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      background: var(--card-inner);
      min-width: 0;
    }}

    .metric-tile span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 4px;
    }}

    .metric-tile strong {{
      display: block;
      color: var(--text);
      font-size: 20px;
      line-height: 1.2;
      overflow-wrap: anywhere;
    }}

    .source-overview {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 16px;
    }}

    .source-chip {{
      --tone: var(--blue);
      --tone-deep: var(--blue-deep);
      --tone-soft: var(--surface-blue);
      --tone-line: #c8ddff;
      display: inline-flex;
      align-items: center;
      min-height: 30px;
      padding: 4px 9px;
      border-radius: 999px;
      border: 1px solid var(--tone-line);
      background: var(--tone-soft);
      color: var(--tone-deep);
      font-size: 12px;
      font-weight: 800;
      text-decoration: none;
      transition: border-color 0.16s ease, box-shadow 0.16s ease, transform 0.16s ease;
    }}

    .source-chip:hover,
    .source-chip:focus-visible {{
      border-color: var(--tone-deep);
      box-shadow: 0 6px 14px rgba(17, 24, 39, 0.08);
      transform: translateY(-1px);
      outline: none;
    }}

    .source-grid {{
      display: grid;
      gap: 18px;
      align-items: start;
    }}

    .source-module {{
      --tone: var(--blue);
      --tone-deep: var(--blue-deep);
      --tone-soft: var(--surface-blue);
      --tone-line: #c8ddff;
      width: 100%;
      background: var(--card);
      border: 1px solid var(--border-strong);
      border-radius: 8px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }}

    .source-module.compact-module {{
      display: block;
    }}

    .theme-blue {{ --tone: var(--blue); --tone-deep: var(--blue-deep); --tone-soft: var(--surface-blue); --tone-line: #c8ddff; }}
    .theme-orange {{ --tone: var(--orange); --tone-deep: var(--orange-deep); --tone-soft: var(--surface-orange); --tone-line: #ffd2a3; }}
    .theme-violet {{ --tone: var(--violet); --tone-deep: var(--violet-deep); --tone-soft: var(--surface-violet); --tone-line: #d7d2ff; }}
    .theme-green {{ --tone: var(--green); --tone-deep: var(--green-deep); --tone-soft: var(--surface-green); --tone-line: #9ee9c8; }}
    .theme-red {{ --tone: var(--red); --tone-deep: var(--red-deep); --tone-soft: var(--surface-red); --tone-line: #f4a9b5; }}
    .theme-teal {{ --tone: var(--teal); --tone-deep: var(--teal-deep); --tone-soft: var(--surface-teal); --tone-line: #a7e4db; }}

    .module-head {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 14px;
      align-items: start;
      padding: 18px 20px;
      background: var(--tone-soft);
      border-bottom: 1px solid var(--tone-line);
    }}

    .module-title {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin: 0 0 6px;
      color: var(--text);
      font-size: 22px;
      line-height: 1.25;
      letter-spacing: 0;
    }}

    .module-title::before {{
      content: "";
      display: inline-block;
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--tone);
      box-shadow: 0 0 0 4px rgba(255, 255, 255, 0.72);
      flex: 0 0 auto;
    }}

    .module-meta {{
      color: var(--muted);
      font-size: 13px;
      overflow-wrap: anywhere;
    }}

    .interface-data {{
      display: grid;
      gap: 8px;
      padding: 16px 20px 20px;
    }}

    .data-row {{
      display: grid;
      grid-template-columns: 34px minmax(0, 1fr) 96px;
      gap: 10px;
      align-items: start;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: #fff;
      padding: 10px;
      min-width: 0;
      transition: border-color 0.16s ease, box-shadow 0.16s ease, transform 0.16s ease;
    }}

    .data-row:hover {{
      border-color: var(--tone-line);
      box-shadow: 0 8px 20px rgba(17, 24, 39, 0.07);
      transform: translateY(-1px);
    }}

    .compact-module .module-head {{
      grid-template-columns: minmax(0, 1fr) auto;
      padding: 14px 16px;
      gap: 10px;
    }}

    .compact-module .module-title {{
      font-size: 19px;
    }}

    .compact-module .module-meta {{
      font-size: 12px;
    }}

    .compact-module .interface-data {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      padding: 12px 16px 16px;
    }}

    .compact-module .interface-data:not(.compact-split) {{
      grid-template-columns: 1fr;
    }}

    .compact-column {{
      display: grid;
      align-content: start;
      gap: 7px;
      min-width: 0;
    }}

    .compact-module .data-row {{
      grid-template-columns: 30px minmax(0, 1fr) 52px;
      gap: 8px;
      padding: 8px;
      min-height: 46px;
    }}

    .compact-module .row-index {{
      width: 26px;
      height: 26px;
      border-radius: 7px;
      font-size: 12px;
    }}

    .compact-module .row-main {{
      gap: 5px;
    }}

    .compact-module .row-title {{
      font-size: 14px;
      line-height: 1.42;
    }}

    .compact-module .row-image {{
      width: 52px;
      border-radius: 7px;
    }}

    .compact-module .meta-row {{
      gap: 4px;
    }}

    .compact-module .meta-pill {{
      min-height: 22px;
      padding: 1px 7px;
      font-size: 11px;
    }}

    .row-index {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 30px;
      height: 30px;
      border-radius: 8px;
      background: var(--tone-soft);
      color: var(--tone-deep);
      border: 1px solid var(--tone-line);
      font-size: 13px;
      font-weight: 900;
    }}

    .row-main {{
      display: grid;
      gap: 7px;
      min-width: 0;
    }}

    .row-title {{
      color: var(--text);
      font-size: 15px;
      line-height: 1.45;
      font-weight: 800;
      text-decoration: none;
      overflow-wrap: anywhere;
      text-wrap: pretty;
    }}

    .row-title:hover {{ color: var(--tone-deep); text-decoration: underline; }}

    .row-image {{
      width: 96px;
      aspect-ratio: 4 / 3;
      object-fit: cover;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: var(--card-inner);
    }}

    .meta-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }}

    .meta-pill {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      border-radius: 999px;
      background: #f2f6fa;
      color: #596579;
      border: 1px solid #e3e9f2;
      padding: 2px 8px;
      font-size: 12px;
      font-weight: 700;
    }}

    .meta-pill.hot {{
      background: var(--surface-orange);
      color: var(--orange-deep);
      border-color: #ffd2a3;
    }}

    .error-panel {{
      margin: 16px 20px 20px;
      border: 1px solid #f4a9b5;
      border-radius: 8px;
      background: var(--surface-red);
      color: var(--red-deep);
      padding: 13px 14px;
      font-size: 14px;
      overflow-wrap: anywhere;
    }}

    .gold-price-module {{
      border: 0;
      background: #17181c;
      color: #f8fafc;
      box-shadow: 0 18px 42px rgba(17, 24, 39, 0.16);
    }}

    .gold-card-shell {{
      padding: 28px;
      background: linear-gradient(180deg, #191a1f 0%, #141519 100%);
      border: 1px solid rgba(255, 203, 30, 0.24);
      border-radius: 8px;
    }}

    .gold-headline {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 16px;
      align-items: start;
      margin-bottom: 12px;
    }}

    .gold-headline h2 {{
      margin: 0;
      color: #ffcb1e;
      font-size: 24px;
      line-height: 1.2;
      letter-spacing: 0;
    }}

    .gold-updated {{
      min-height: 30px;
      padding: 4px 9px;
      border-radius: 8px;
      border: 1px solid rgba(255, 203, 30, 0.28);
      color: #f6d87a;
      background: rgba(255, 203, 30, 0.08);
      font-size: 12px;
      font-weight: 800;
      white-space: nowrap;
    }}

    .gold-price-row {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 18px;
      align-items: end;
      margin: 4px 0 18px;
    }}

    .gold-price-value {{
      display: flex;
      align-items: baseline;
      gap: 8px;
      min-width: 0;
      color: #ffcb1e;
    }}

    .gold-price-value span {{
      font-size: 54px;
      line-height: 0.96;
      font-weight: 950;
      letter-spacing: 0;
    }}

    .gold-price-value em {{
      font-style: normal;
      font-size: 18px;
      font-weight: 950;
    }}

    .gold-delta {{
      min-height: 34px;
      padding: 5px 11px;
      border-radius: 8px;
      font-size: 14px;
      font-weight: 900;
      border: 1px solid rgba(255, 203, 30, 0.22);
      background: rgba(255, 255, 255, 0.06);
      color: #ffcb1e;
    }}

    .gold-delta.down,
    .gold-chart-head strong.down {{ color: #6ee7b7; }}
    .gold-delta.up,
    .gold-chart-head strong.up {{ color: #ffcb1e; }}

    .gold-metrics {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 16px;
    }}

    .gold-metrics div {{
      min-width: 0;
      padding: 10px 12px;
      border-radius: 8px;
      border: 1px solid rgba(255, 203, 30, 0.16);
      background: rgba(255, 255, 255, 0.05);
    }}

    .gold-metrics span {{
      display: block;
      color: #a6adbb;
      font-size: 12px;
      margin-bottom: 3px;
    }}

    .gold-metrics strong {{
      display: block;
      color: #f8fafc;
      font-size: 15px;
      overflow-wrap: anywhere;
    }}

    .gold-chart-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
    }}

    .gold-chart-card {{
      min-width: 0;
      border-radius: 8px;
      border: 1px solid rgba(255, 203, 30, 0.18);
      background: #1f2026;
      padding: 12px;
      overflow: hidden;
    }}

    .gold-chart-head {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      align-items: start;
      margin-bottom: 8px;
    }}

    .gold-chart-head span {{
      display: block;
      color: #ffcb1e;
      font-size: 15px;
      font-weight: 950;
    }}

    .gold-chart-head small {{
      display: block;
      color: #a6adbb;
      font-size: 12px;
    }}

    .gold-chart-head strong {{
      font-size: 13px;
      font-weight: 950;
      white-space: nowrap;
    }}

    .gold-trend-svg {{
      display: block;
      width: 100%;
      height: 148px;
    }}

    .gold-grid-line {{ stroke: rgba(255, 203, 30, 0.16); stroke-width: 1; }}
    .gold-line {{ stroke: #ffcb1e; stroke-width: 3; stroke-linecap: round; stroke-linejoin: round; fill: none; }}
    .gold-area {{ fill: rgba(255, 203, 30, 0.18); stroke: none; }}
    .gold-bars rect {{ fill: #ffcb1e; opacity: 0.78; }}

    .gold-chart-empty {{
      min-height: 148px;
      display: grid;
      place-items: center;
      color: #a6adbb;
      font-size: 13px;
    }}

    .gold-source-note {{
      margin: 12px 0 0;
      color: #8e96a7;
      font-size: 12px;
      overflow-wrap: anywhere;
    }}

    .site-footer {{
      color: var(--muted);
      font-size: 13px;
      text-align: center;
      margin-top: 24px;
    }}

    .site-footer a {{
      color: inherit;
      text-decoration: none;
      border-bottom: 1px solid rgba(101, 112, 131, 0.28);
    }}

    .site-footer a:hover,
    .site-footer a:focus-visible {{
      color: var(--blue-deep);
      border-bottom-color: currentColor;
      outline: none;
    }}

    .back-to-top {{
      position: fixed;
      right: max(24px, calc((100vw - var(--page-max)) / 2 - 58px));
      bottom: 18px;
      z-index: 20;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 42px;
      height: 42px;
      border-radius: 999px;
      border: 1px solid var(--border-strong);
      background: rgba(255, 255, 255, 0.92);
      color: var(--blue-deep);
      box-shadow: var(--shadow);
      font-size: 20px;
      font-weight: 900;
      text-decoration: none;
      backdrop-filter: blur(8px);
    }}

    .back-to-top:hover,
    .back-to-top:focus-visible {{
      border-color: var(--blue);
      outline: none;
    }}

    @media (prefers-reduced-motion: reduce) {{
      .data-row {{ transition: none; }}
      .data-row:hover {{ transform: none; }}
    }}
  </style>
</head>
<body>
  <main id="top" class="page">
    <header class="report-head">
      <div class="title-line">
        <div>
          <h1>新闻日报</h1>
          <p class="subhead">按来源分组展示最新要闻、热榜与科技新闻</p>
        </div>
      </div>
      <div class="metric-strip">
        <div class="metric-tile"><span>接口卡片</span><strong>{visible_count}</strong></div>
        <div class="metric-tile"><span>收录新闻</span><strong>{total_items} 条</strong></div>
        <div class="metric-tile"><span>更新时间</span><strong>{escape(generated_time)}</strong></div>
        <div class="metric-tile"><span>文件时区</span><strong>北京时间</strong></div>
      </div>
      {source_overview}
    </header>
    {section_grid}
    {render_project_footer()}
  </main>
  <a class="back-to-top" href="#top" aria-label="回到顶部" title="回到顶部">↑</a>
</body>
</html>
"""


def render_empty_state() -> str:
    return """
    <section class="source-module theme-red">
      <div class="module-head">
        <div>
          <h2 class="module-title">暂无可展示新闻</h2>
          <div class="module-meta">本次没有查询到可用于页面展示的有效数据</div>
        </div>
      </div>
      <div class="error-panel">请稍后重新生成，或检查当前网络与数据源可用性。</div>
    </section>
"""


def render_source_chip(result: SourceResult, index: int) -> str:
    theme_class = SOURCE_THEME_CLASSES[index % len(SOURCE_THEME_CLASSES)]
    status = "成功" if result.ok else "失败"
    return f'<a class="source-chip {theme_class}" href="#{source_anchor(index)}">{escape(result.name)} · {status} · {len(result.items)} 条</a>'


def render_gold_price_card(result: SourceResult, index: int) -> str:
    data = result.data
    price = parse_float(data.get("price")) or 0.0
    change = parse_float(data.get("change")) or 0.0
    change_percent = parse_float(data.get("change_percent")) or 0.0
    price_usd = parse_float(data.get("price_usd")) or 0.0
    fx_rate = parse_float(data.get("fx_rate")) or 0.0
    updated = clean_text(data.get("updated"))
    delta_class = "up" if change >= 0 else "down"
    delta_sign = "+" if change >= 0 else ""
    charts = data.get("charts") if isinstance(data.get("charts"), list) else []
    chart_cards = "".join(render_gold_chart_card(chart) for chart in charts if isinstance(chart, dict))
    source_line = "数据源：集金号现货黄金 JO_92233；汇率：东方财富 USDCNH。"
    return f"""
    <section id="{source_anchor(index)}" class="source-module gold-price-module">
      <div class="gold-card-shell">
        <div class="gold-headline">
          <div>
            <h2>黄金价格</h2>
          </div>
          <div class="gold-updated">{escape(updated or "实时行情")}</div>
        </div>
        <div class="gold-price-row">
          <div class="gold-price-value"><span>{price:.2f}</span><em>¥/克</em></div>
          <div class="gold-delta {delta_class}">{delta_sign}{change:.2f} · {delta_sign}{change_percent:.2f}%</div>
        </div>
        <div class="gold-metrics">
          <div><span>现货黄金</span><strong>{price_usd:.2f} 美元/盎司</strong></div>
          <div><span>美元兑离岸人民币</span><strong>{fx_rate:.4f}</strong></div>
          <div><span>换算口径</span><strong>人民币/克</strong></div>
        </div>
        <div class="gold-chart-grid">
          {chart_cards}
        </div>
        <p class="gold-source-note">{escape(source_line)}</p>
      </div>
    </section>
"""


def render_gold_chart_card(chart: dict[str, Any]) -> str:
    label = clean_text(chart.get("label"))
    caption = clean_text(chart.get("caption"))
    variant = clean_text(chart.get("variant")) or "line"
    points = chart.get("points") if isinstance(chart.get("points"), list) else []
    values = [parse_float(point.get("value")) for point in points if isinstance(point, dict)]
    values = [value for value in values if value is not None]
    current = values[-1] if values else 0.0
    first = values[0] if values else current
    delta = current - first
    delta_class = "up" if delta >= 0 else "down"
    delta_sign = "+" if delta >= 0 else ""
    svg = render_gold_trend_svg(points, variant)
    return f"""
          <div class="gold-chart-card {escape(variant)}">
            <div class="gold-chart-head">
              <div>
                <span>{escape(label)}</span>
                <small>{escape(caption)}</small>
              </div>
              <strong class="{delta_class}">{delta_sign}{delta:.2f}</strong>
            </div>
            {svg}
          </div>
"""


def render_gold_trend_svg(points: list[Any], variant: str) -> str:
    clean_points = [point for point in points if isinstance(point, dict) and parse_float(point.get("value")) is not None]
    values = [float(point["value"]) for point in clean_points]
    if len(values) < 2:
        return '<div class="gold-chart-empty">暂无趋势数据</div>'
    width = 360
    height = 148
    pad_x = 14
    pad_y = 16
    min_value = min(values)
    max_value = max(values)
    spread = max(max_value - min_value, 0.01)

    coords: list[tuple[float, float]] = []
    for index, value in enumerate(values):
        x = pad_x + index * (width - pad_x * 2) / (len(values) - 1)
        y = height - pad_y - ((value - min_value) / spread) * (height - pad_y * 2)
        coords.append((x, y))
    line_points = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    line_path = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in coords)

    if variant == "bars":
        bar_gap = 1.6
        bar_width = max(1.4, (width - pad_x * 2) / len(values) - bar_gap)
        baseline = height - pad_y
        bars = []
        for x, y in coords:
            bar_height = max(2.0, baseline - y)
            bars.append(f'<rect x="{x - bar_width / 2:.1f}" y="{baseline - bar_height:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" rx="1.2" />')
        body = '<g class="gold-bars">' + "".join(bars) + "</g>"
    elif variant == "area":
        baseline = height - pad_y
        area_path = f"M {coords[0][0]:.1f} {baseline:.1f} L " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in coords) + f" L {coords[-1][0]:.1f} {baseline:.1f} Z"
        body = f'<path class="gold-area" d="{area_path}"/><path class="gold-line" d="{line_path}"/>'
    else:
        body = f'<polyline class="gold-line" points="{line_points}" fill="none"/>'

    first_label = clean_text(clean_points[0].get("label"))
    last_label = clean_text(clean_points[-1].get("label"))
    return f"""
            <svg class="gold-trend-svg" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(first_label)} 到 {escape(last_label)} 的黄金价格趋势">
              <line class="gold-grid-line" x1="{pad_x}" y1="{height - pad_y}" x2="{width - pad_x}" y2="{height - pad_y}" />
              {body}
            </svg>
"""


def render_news_source_cards(result: SourceResult, index: int) -> str:
    if result.data.get("kind") == "gold_price":
        return render_gold_price_card(result, index)

    theme_class = SOURCE_THEME_CLASSES[index % len(SOURCE_THEME_CLASSES)]
    is_compact = is_compact_source(result)
    layout_class = " compact-module" if is_compact else ""
    body = render_news_item_body(result.items, is_compact)
    return f"""
    <section id="{source_anchor(index)}" class="source-module {theme_class}{layout_class}">
      <div class="module-head">
        <div>
          <h2 class="module-title">{escape(result.name)}</h2>
          <div class="module-meta">{escape(result.method)} · {escape(result.url)}</div>
        </div>
      </div>
      {body}
    </section>
"""


def render_news_item_body(items: list[NewsItem], is_compact: bool) -> str:
    if not is_compact or len(items) < 2:
        rows = "".join(render_news_item_row(item, item_index + 1) for item_index, item in enumerate(items))
        return f'<div class="interface-data">{rows}</div>'

    split_index = (len(items) + 1) // 2
    columns = []
    for start_index, column_items in ((0, items[:split_index]), (split_index, items[split_index:])):
        if not column_items:
            continue
        rows = "".join(
            render_news_item_row(item, start_index + item_index + 1)
            for item_index, item in enumerate(column_items)
        )
        columns.append(f'<div class="compact-column">{rows}</div>')
    return '<div class="interface-data compact-split">' + "".join(columns) + "</div>"


def render_news_item_row(item: NewsItem, index: int) -> str:
    title = escape(item.title)
    if item.url:
        title_html = f'<a class="row-title" href="{escape(item.url, quote=True)}" target="_blank" rel="noopener noreferrer">{title}</a>'
    else:
        title_html = f'<span class="row-title">{title}</span>'
    meta_values = [value for value in [item.hot, *item.meta] if value]
    meta_html = ""
    if meta_values:
        meta_html = '<div class="meta-row">' + "".join(
            f'<span class="meta-pill {"hot" if value == item.hot else ""}">{escape(value)}</span>'
            for value in meta_values
        ) + "</div>"
    image_html = ""
    if item.image:
        image_html = f'<img class="row-image" src="{escape(item.image, quote=True)}" alt="" loading="lazy">'
    return f"""
        <div class="data-row">
          <span class="row-index">{index}</span>
          <div class="row-main">
            {title_html}
            {meta_html}
          </div>
          {image_html}
        </div>
"""


def find_browser_executable() -> Path | None:
    env_path = clean_text(os.environ.get("NEWS_HTML_BROWSER"))
    candidates: list[str] = []
    if env_path:
        candidates.append(env_path)
    for name in ("msedge", "chrome", "chromium", "brave"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    candidates.extend(
        [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    )
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return path
    return None


def capture_long_screenshot(html_path: Path, width: int, height: int, timeout: int) -> tuple[Path | None, str]:
    browser = find_browser_executable()
    if not browser:
        return None, "no Edge/Chrome-compatible browser executable found"

    screenshot_path = html_path.with_suffix(".png")
    if screenshot_path.exists():
        screenshot_path.unlink()

    with tempfile.TemporaryDirectory(prefix="news-html-browser-") as temp_dir:
        temp_root = Path(temp_dir)
        user_data_dir = temp_root / "profile"
        screenshot_html_path = temp_root / html_path.name
        screenshot_html = html_path.read_text(encoding="utf-8", errors="replace")
        screenshot_hide_style = '<style id="news-screenshot-overrides">.back-to-top{display:none!important;}</style>'
        if "</head>" in screenshot_html:
            screenshot_html = screenshot_html.replace("</head>", f"{screenshot_hide_style}\n</head>", 1)
        else:
            screenshot_html = screenshot_hide_style + screenshot_html
        screenshot_html_path.write_text(screenshot_html, encoding="utf-8")
        command = [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-default-browser-check",
            "--allow-file-access-from-files",
            f"--user-data-dir={user_data_dir}",
            f"--window-size={width},{height}",
            f"--screenshot={screenshot_path}",
            screenshot_html_path.resolve().as_uri(),
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return None, f"browser screenshot timed out after {timeout}s"

    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace") if completed.stderr else ""
        stdout = completed.stdout.decode("utf-8", errors="replace") if completed.stdout else ""
        details = clean_text((stderr or stdout or "browser exited with an error")[:500], 360)
        return None, details
    if not screenshot_path.is_file() or screenshot_path.stat().st_size == 0:
        return None, "browser did not create a screenshot file"

    crop_screenshot_bottom(screenshot_path)
    return screenshot_path, ""


def crop_screenshot_bottom(path: Path) -> None:
    try:
        from PIL import Image
    except Exception:
        return

    try:
        image = Image.open(path).convert("RGB")
        width, height = image.size
        last_content_y = height - 1
        for y in range(height - 1, 0, -8):
            samples = [image.getpixel((x, y)) for x in range(0, width, 24)]
            if not samples:
                continue
            channel_ranges = [max(pixel[idx] for pixel in samples) - min(pixel[idx] for pixel in samples) for idx in range(3)]
            if max(channel_ranges) > 18 or sum(channel_ranges) > 42:
                last_content_y = y
                break
        crop_bottom = min(height, last_content_y + 64)
        if crop_bottom < height - 80:
            image.crop((0, 0, width, crop_bottom)).save(path)
    except Exception:
        return


def generate_report(args: argparse.Namespace) -> tuple[Path, Path | None, str, list[SourceResult]]:
    generated_at = datetime.now(BEIJING_TZ)
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = generated_at.strftime("%Y%m%d-%H%M%S.html")
    output_path = output_dir / filename

    results = [
        parse_jinzanzan_gold_price(args.timeout, args.retries),
        parse_60s(args.limit, args.timeout, args.retries),
        parse_weibo(args.limit, args.timeout, args.retries),
        parse_zhihu(args.limit, args.timeout, args.retries),
        parse_douyin_hot(args.limit, args.timeout, args.retries),
        parse_tencent(args.limit, args.timeout, args.retries),
        parse_thepaper(args.limit, args.timeout, args.retries, args.paper_node_id),
        parse_eastmoney_stock_news(args.limit, args.timeout, args.retries),
        parse_bilibili_hot_search(args.limit, args.timeout, args.retries),
        parse_bilibili_popular(args.limit, args.timeout, args.retries),
        parse_ithome(args.limit, args.timeout, args.retries),
        parse_sspai(args.limit, args.timeout, args.retries),
        parse_juejin(args.limit, args.timeout, args.retries),
        parse_baidu_hot(args.limit, args.timeout, args.retries),
        parse_toutiao_hot(args.limit, args.timeout, args.retries),
        parse_hackernews(args.limit, args.timeout, args.retries),
        parse_cankaoxiaoxi(args.limit, args.timeout, args.retries),
        parse_solidot(args.limit, args.timeout, args.retries),
        parse_cls_hot(args.limit, args.timeout, args.retries),
        parse_cls_telegraph(args.limit, args.timeout, args.retries),
    ]
    html_text = render_news_card_html(results, generated_at)
    output_path.write_text(html_text, encoding="utf-8", newline="\n")
    screenshot_path: Path | None = None
    screenshot_error = ""
    if args.screenshot:
        screenshot_path, screenshot_error = capture_long_screenshot(
            output_path,
            args.screenshot_width,
            args.screenshot_height,
            args.screenshot_timeout,
        )
    return output_path, screenshot_path, screenshot_error, results


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Chinese news sources and generate a Beijing-time HTML digest.")
    parser.add_argument("--output-dir", default=".", help="Directory for the generated HTML file.")
    parser.add_argument("--limit", type=source_limit, default=DEFAULT_LIMIT, help="Maximum items per source, from 10 to 20.")
    parser.add_argument("--timeout", type=positive_int, default=DEFAULT_TIMEOUT, help="Seconds per request attempt.")
    parser.add_argument("--retries", type=non_negative_int, default=DEFAULT_RETRIES, help="Retries per source after the first attempt.")
    parser.add_argument("--paper-node-id", default="25950", help="ThePaper nodeId for nodeCont/getByNodeIdPortal.")
    screenshot_group = parser.add_mutually_exclusive_group()
    screenshot_group.add_argument("--screenshot", dest="screenshot", action="store_true", default=None, help="Capture a long PNG screenshot next to the generated HTML file for this run.")
    screenshot_group.add_argument("--no-screenshot", dest="screenshot", action="store_false", help="Generate only the HTML file.")
    parser.add_argument("--remember-screenshot-preference", type=screenshot_preference, metavar="{yes,no}", help="Persist the default screenshot choice for future runs.")
    parser.add_argument("--print-preferences", action="store_true", help="Print saved preferences and exit without generating a report.")
    parser.add_argument("--screenshot-width", type=positive_int, default=DEFAULT_SCREENSHOT_WIDTH, help="Screenshot viewport width in pixels.")
    parser.add_argument("--screenshot-height", type=positive_int, default=DEFAULT_SCREENSHOT_HEIGHT, help="Maximum screenshot viewport height in pixels.")
    parser.add_argument("--screenshot-timeout", type=positive_int, default=DEFAULT_SCREENSHOT_TIMEOUT, help="Seconds to wait for browser screenshot capture.")
    args = parser.parse_args(argv)
    apply_screenshot_preference(args)
    return args


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def source_limit(value: str) -> int:
    parsed = positive_int(value)
    if parsed < 10 or parsed > 20:
        raise argparse.ArgumentTypeError("must be between 10 and 20")
    return parsed


def screenshot_preference(value: str) -> bool:
    normalized = clean_text(value).lower()
    truthy = {"1", "true", "yes", "y", "on", "需要", "要", "生成"}
    falsy = {"0", "false", "no", "n", "off", "不需要", "不要", "不生成"}
    if normalized in truthy:
        return True
    if normalized in falsy:
        return False
    raise argparse.ArgumentTypeError("must be yes or no")


def apply_screenshot_preference(args: argparse.Namespace) -> None:
    if args.remember_screenshot_preference is not None:
        write_preferences({SCREENSHOT_PREFERENCE_KEY: args.remember_screenshot_preference})
        if args.screenshot is None:
            args.screenshot = args.remember_screenshot_preference
    if args.screenshot is None:
        saved_preference = read_screenshot_preference()
        args.screenshot = saved_preference if saved_preference is not None else True


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be greater than or equal to 0")
    return parsed


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.print_preferences:
        print_preferences()
        return 0
    output_path, screenshot_path, screenshot_error, results = generate_report(args)
    print(f"OUTPUT: {output_path}")
    if screenshot_path:
        print(f"SCREENSHOT: {screenshot_path}")
    elif args.screenshot:
        print(f"SCREENSHOT_FAIL: {screenshot_error or 'unknown screenshot error'}")
    for result in results:
        status = "OK" if result.ok else "FAIL"
        detail = f"{len(result.items)} items" if result.ok else result.error
        print(f"[{status}] {result.name}: {detail}")
    return 0 if any(result.ok for result in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
