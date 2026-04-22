"""
Petit Pets - 每周自动化报告脚本
-----------------------------------
每周日自动运行，从 Shopify / YouTube / Apify 拉数据，
经 Claude 分析后把结果写入 Google Sheet。

本地测试：python weekly_report.py
"""

import os
import json
import sys
import traceback
from datetime import datetime, timedelta

import requests
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build as google_build


# ============================================================
# 配置（对标账号 / 关键词 — 可以自由增删）
# ============================================================

TIKTOK_ACCOUNTS = [
    "birb_ney",
    "funnyparrotlife",
    "rachelthegaloah",
    "bird_nerd",
    "piloti.the.conure",
    # 异宠/小宠赛道
    "hammies.at.home",
    "mr.pokee",
    "bunnies.stories",
]

TIKTOK_KEYWORDS = [
    "bird carrier",
    "parrot backpack",
    "hamster aesthetic",
    "bunny harness",
    "exotic pet",
]

YOUTUBE_KEYWORDS = [
    "bird carrier review",
    "parrot travel bag",
    "hamster cage setup",
    "bunny harness",
]

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")


# ============================================================
# 工具函数
# ============================================================

def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def claude_chat(prompt: str, max_tokens: int = 3000) -> str:
    """使用 Claude API（兼容 Anthropic 原生格式 & OpenAI 兼容代理）"""
    base = os.environ["ANTHROPIC_BASE_URL"].rstrip("/")
    token = os.environ["ANTHROPIC_AUTH_TOKEN"]

    # 两种常见端点：/v1/messages（Anthropic 原生）和 /v1/chat/completions（OpenAI 兼容）
    # 先试原生，失败再试 OpenAI 兼容
    headers_native = {
        "x-api-key": token,
        "authorization": f"Bearer {token}",  # 一些代理要 Bearer
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload_native = {
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        r = requests.post(f"{base}/v1/messages", headers=headers_native, json=payload_native, timeout=180)
        r.raise_for_status()
        data = r.json()
    except Exception:
        # 退回到 OpenAI 兼容端点
        headers_openai = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        }
        payload_openai = {
            "model": CLAUDE_MODEL,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        r = requests.post(f"{base}/v1/chat/completions", headers=headers_openai, json=payload_openai, timeout=180)
        r.raise_for_status()
        data = r.json()

    # 解析：兼容多种格式
    # Anthropic 原生: {"content": [{"type": "text"|"thinking", "text": "..."}, ...]}
    # 注意：启用 extended thinking 时，content 会同时包含 thinking 块和 text 块，要跳过 thinking
    if isinstance(data.get("content"), list) and data["content"]:
        text_parts = []
        for block in data["content"]:
            if not isinstance(block, dict):
                continue
            # 跳过 thinking / redacted_thinking 块
            if block.get("type") in ("thinking", "redacted_thinking"):
                continue
            if "text" in block and isinstance(block["text"], str):
                text_parts.append(block["text"])
        if text_parts:
            return "\n\n".join(text_parts)
    # OpenAI 兼容: {"choices": [{"message": {"content": "..."}}]}
    if isinstance(data.get("choices"), list) and data["choices"]:
        choice = data["choices"][0]
        if isinstance(choice, dict):
            msg = choice.get("message")
            if isinstance(msg, dict) and "content" in msg:
                content = msg["content"]
                if isinstance(content, str):
                    return content
                # 有些实现 content 是 list
                if isinstance(content, list):
                    parts = [c.get("text", "") for c in content if isinstance(c, dict)]
                    if parts:
                        return "\n\n".join(parts)
            if "text" in choice and isinstance(choice["text"], str):
                return choice["text"]
    # 兜底：直接 content 是字符串
    if isinstance(data.get("content"), str):
        return data["content"]

    raise RuntimeError(f"Claude 返回格式未知: {json.dumps(data, ensure_ascii=False)[:500]}")


# ============================================================
# Shopify
# ============================================================

def fetch_shopify_products() -> list:
    shop = os.environ["SHOPIFY_SHOP_URL"]
    token = os.environ["SHOPIFY_ACCESS_TOKEN"]
    url = f"https://{shop}/admin/api/2024-10/products.json?limit=100"
    r = requests.get(url, headers={"X-Shopify-Access-Token": token}, timeout=60)
    r.raise_for_status()
    return r.json().get("products", [])


def fetch_shopify_orders(days: int = 7) -> list:
    shop = os.environ["SHOPIFY_SHOP_URL"]
    token = os.environ["SHOPIFY_ACCESS_TOKEN"]
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    url = (
        f"https://{shop}/admin/api/2024-10/orders.json"
        f"?created_at_min={since}&status=any&limit=250"
    )
    r = requests.get(url, headers={"X-Shopify-Access-Token": token}, timeout=60)
    r.raise_for_status()
    return r.json().get("orders", [])


# ============================================================
# YouTube Data API v3
# ============================================================

def fetch_youtube_top(keyword: str, max_results: int = 10) -> list:
    yt = google_build("youtube", "v3", developerKey=os.environ["YOUTUBE_API_KEY"])
    published_after = (
        (datetime.utcnow() - timedelta(days=7)).isoformat("T") + "Z"
    )
    search = yt.search().list(
        q=keyword,
        part="snippet",
        type="video",
        videoDuration="short",
        order="viewCount",
        publishedAfter=published_after,
        maxResults=max_results,
    ).execute()
    items = search.get("items", [])
    video_ids = [it["id"]["videoId"] for it in items if it.get("id", {}).get("videoId")]
    if not video_ids:
        return []

    stats_resp = yt.videos().list(
        part="statistics,snippet", id=",".join(video_ids)
    ).execute()
    stats_map = {s["id"]: s for s in stats_resp.get("items", [])}

    results = []
    for it in items:
        vid = it["id"].get("videoId")
        if not vid:
            continue
        s = stats_map.get(vid, {})
        stats = s.get("statistics", {})
        results.append({
            "title": it["snippet"]["title"],
            "channel": it["snippet"]["channelTitle"],
            "url": f"https://youtube.com/shorts/{vid}",
            "views": int(stats.get("viewCount", 0)),
            "likes": int(stats.get("likeCount", 0)),
            "published": it["snippet"]["publishedAt"],
        })
    return sorted(results, key=lambda x: x["views"], reverse=True)


# ============================================================
# Apify - TikTok
# ============================================================

def fetch_tiktok_via_apify(
    handles: list = None,
    keywords: list = None,
    limit_per_query: int = 10,
) -> list:
    token = os.environ["APIFY_TOKEN"]
    all_results = []
    queries = []
    if handles:
        queries.extend([("profile", h) for h in handles])
    if keywords:
        queries.extend([("search", k) for k in keywords])

    for qtype, qval in queries:
        if qtype == "profile":
            input_data = {
                "profiles": [qval],
                "resultsPerPage": limit_per_query,
                "shouldDownloadVideos": False,
                "shouldDownloadCovers": False,
                "shouldDownloadSubtitles": False,
            }
        else:
            input_data = {
                "searchQueries": [qval],
                "resultsPerPage": limit_per_query,
                "shouldDownloadVideos": False,
                "shouldDownloadCovers": False,
                "shouldDownloadSubtitles": False,
            }

        run_url = (
            "https://api.apify.com/v2/acts/clockworks~tiktok-scraper/"
            f"run-sync-get-dataset-items?token={token}"
        )
        try:
            r = requests.post(run_url, json=input_data, timeout=300)
            r.raise_for_status()
            data = r.json()
            for item in data:
                all_results.append({
                    "source": qtype,
                    "query": qval,
                    "author": item.get("authorMeta", {}).get("name", ""),
                    "description": (item.get("text") or "")[:200],
                    "plays": item.get("playCount", 0),
                    "likes": item.get("diggCount", 0),
                    "shares": item.get("shareCount", 0),
                    "comments": item.get("commentCount", 0),
                    "url": item.get("webVideoUrl", ""),
                    "music": (item.get("musicMeta") or {}).get("musicName", ""),
                    "created": item.get("createTimeISO", ""),
                })
        except Exception as e:
            log(f"   ⚠️ Apify query {qtype}:{qval} 失败: {e}")

    return sorted(all_results, key=lambda x: x["plays"], reverse=True)


# ============================================================
# Google Sheets 写入
# ============================================================

def get_sheet():
    creds_json = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    sheet_url = os.environ["GOOGLE_SHEET_URL"]
    # 诊断日志（帮我们看清楚"代码在用什么身份，打开什么表"）
    log(f"   🔍 Bot email: {creds_json.get('client_email')}")
    log(f"   🔍 Project:   {creds_json.get('project_id')}")
    log(f"   🔍 Sheet URL: {sheet_url}")
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_url(sheet_url)


def _clean_cell(v):
    """把 None / dict / list 转成 gspread 能接受的纯值"""
    if v is None:
        return ""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, (dict, list)):
        try:
            return json.dumps(v, ensure_ascii=False)[:500]
        except Exception:
            return str(v)[:500]
    return str(v)[:50000]  # gspread 单 cell 字符上限


def write_tab(sh, tab_name: str, headers: list, rows: list) -> None:
    try:
        ws = sh.worksheet(tab_name)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(tab_name, rows=1000, cols=max(len(headers), 10))
    ws.append_row([_clean_cell(h) for h in headers])
    if rows:
        cleaned = [[_clean_cell(c) for c in row] for row in rows]
        # gspread 不允许单次过多行，分批
        batch_size = 500
        for i in range(0, len(cleaned), batch_size):
            ws.append_rows(cleaned[i:i + batch_size])


# ============================================================
# 主流程
# ============================================================

def main() -> None:
    log("🚀 开始本周自动化任务")

    # 1. Shopify 产品 + 订单
    log("1/5 拉取 Shopify 产品...")
    try:
        products = fetch_shopify_products()
        log(f"   ✅ 拿到 {len(products)} 个产品")
    except Exception as e:
        log(f"   ⚠️ Shopify 产品失败: {e}")
        products = []

    log("2/5 拉取最近 7 天订单...")
    try:
        orders = fetch_shopify_orders(days=7)
        total_gmv = sum(float(o.get("total_price", 0)) for o in orders)
        log(f"   ✅ 拿到 {len(orders)} 个订单, GMV ${total_gmv:.2f}")
    except Exception as e:
        log(f"   ⚠️ Shopify 订单失败: {e}")
        orders = []

    # 2. YouTube 爆款
    log("3/5 拉取 YouTube Shorts 爆款...")
    yt_results = []
    for kw in YOUTUBE_KEYWORDS:
        try:
            hits = fetch_youtube_top(kw, max_results=5)
            for h in hits:
                h["keyword"] = kw
                yt_results.append(h)
        except Exception as e:
            log(f"   ⚠️ YouTube '{kw}' 失败: {e}")
    log(f"   ✅ 拿到 {len(yt_results)} 条 Shorts")

    # 3. TikTok via Apify（限制初期调用省 credit）
    log("4/5 拉取 TikTok 爆款（Apify）...")
    try:
        tt_results = fetch_tiktok_via_apify(
            handles=TIKTOK_ACCOUNTS[:4],   # 前 4 个账号
            keywords=TIKTOK_KEYWORDS[:2],  # 前 2 个关键词
            limit_per_query=10,
        )
        log(f"   ✅ 拿到 {len(tt_results)} 条 TikTok")
    except Exception as e:
        log(f"   ⚠️ Apify 失败: {e}")
        tt_results = []

    # 4. Claude 分析 + 生成本周脚本
    log("5/5 Claude 分析 + 生成脚本...")
    tt_summary = "\n".join([
        f"- [{v['plays']:,} plays] @{v['author']}: {v['description'][:80]}"
        for v in tt_results[:10]
    ]) or "（无数据）"
    yt_summary = "\n".join([
        f"- [{v['views']:,} views] {v['title']}"
        for v in yt_results[:5]
    ]) or "（无数据）"
    product_titles = ", ".join([p.get("title", "") for p in products[:15]]) or "（无数据）"

    analysis_prompt = f"""你是 Petit Pets（鸟类/异宠/小宠时尚品牌）的增长顾问。
品牌定位：人宠时尚，中高端，客单 $50-100，欧美受众。
基于下列本周爆款情报和产品库，请输出：

## 1. 本周主推推荐
应该主推哪 1-2 个产品？为什么？给数据依据。

## 2. 可复刻的 3 个爆款钩子
从下方 TikTok/YouTube 爆款里提炼。

## 3. 本周 7 条短视频脚本（15 秒内）
每条包含：
- Hook (0-2s)：英文字幕
- Middle (2-10s)：画面描述 + 英文字幕
- CTA (10-13s)：结尾 + petitpets.shop
- BGM 方向
- 对应产品

---

**TikTok 本周 Top 10**：
{tt_summary}

**YouTube Shorts 本周 Top 5**：
{yt_summary}

**产品库**：
{product_titles}
"""

    try:
        analysis = claude_chat(analysis_prompt, max_tokens=4000)
        log(f"   ✅ Claude 分析完成 ({len(analysis)} 字)")
    except Exception as e:
        log(f"   ⚠️ Claude 失败: {e}")
        analysis = f"[分析失败: {e}]"

    # 5. 写入 Google Sheet
    log("💾 写入 Google Sheet...")
    try:
        sh = get_sheet()
    except Exception as e:
        log(f"   ❌ Sheet 授权失败: {type(e).__name__}: {e}")
        log(traceback.format_exc())
        raise

    tabs = [
        ("自动_Shopify产品",
         ["ID", "标题", "类型", "价格", "库存", "状态", "创建时间"],
         [
             [
                 p.get("id"),
                 p.get("title"),
                 p.get("product_type"),
                 (p.get("variants") or [{}])[0].get("price"),
                 (p.get("variants") or [{}])[0].get("inventory_quantity"),
                 p.get("status"),
                 (p.get("created_at") or "")[:10],
             ] for p in products
         ]),
        ("自动_Shopify订单7天",
         ["订单号", "金额", "商品数", "邮箱", "国家", "创建时间"],
         [
             [
                 o.get("name"),
                 o.get("total_price"),
                 len(o.get("line_items") or []),
                 o.get("email"),
                 (o.get("shipping_address") or {}).get("country"),
                 (o.get("created_at") or "")[:10],
             ] for o in orders
         ]),
        ("自动_TikTok爆款",
         ["来源", "查询", "账号", "描述", "播放量", "点赞", "分享", "评论", "BGM", "链接"],
         [
             [
                 r.get("source"), r.get("query"), r.get("author"), r.get("description"),
                 r.get("plays"), r.get("likes"), r.get("shares"), r.get("comments"),
                 r.get("music"), r.get("url"),
             ] for r in tt_results[:50]
         ]),
        ("自动_YouTube爆款",
         ["关键词", "标题", "频道", "观看", "点赞", "发布", "链接"],
         [
             [
                 r.get("keyword"), r.get("title"), r.get("channel"),
                 r.get("views"), r.get("likes"), (r.get("published") or "")[:10], r.get("url"),
             ] for r in yt_results[:50]
         ]),
        ("自动_本周Claude分析",
         ["时间", "分析全文"],
         [[datetime.now().strftime("%Y-%m-%d %H:%M"), analysis]]),
    ]

    success_count = 0
    for tab_name, hdrs, rws in tabs:
        try:
            write_tab(sh, tab_name, hdrs, rws)
            log(f"   ✅ {tab_name} ({len(rws)} 行)")
            success_count += 1
        except Exception as e:
            log(f"   ⚠️ {tab_name} 写入失败: {type(e).__name__}: {e}")
            log(traceback.format_exc())

    log(f"   Google Sheet 完成 {success_count}/{len(tabs)} 个标签页")

    log(
        f"🎉 全部完成 | 产品 {len(products)} / 订单 {len(orders)} / "
        f"TikTok {len(tt_results)} / YouTube {len(yt_results)}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"❌ 致命错误: {type(e).__name__}: {e}")
        log(traceback.format_exc())
        sys.exit(1)
