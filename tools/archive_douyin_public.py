#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any, Iterable

import requests
from playwright.async_api import BrowserContext, Page, async_playwright

SHORT_URL = "https://v.douyin.com/0Mybz_ScO3Q/"
AWEME_ID = "7666692683432429819"
DIRECT_URLS = [
    f"https://www.douyin.com/note/{AWEME_ID}",
    f"https://www.douyin.com/video/{AWEME_ID}",
    f"https://www.iesdouyin.com/share/note/{AWEME_ID}/",
]
ROOT = Path("douyin_output")
RAW = ROOT / "raw"
DEBUG = ROOT / "debug"
MEDIA = ROOT / "media"
COMMENTS = ROOT / "author_image_comments"
TZ = dt.timezone(dt.timedelta(hours=8))
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Safari/537.36"
)

for p in (ROOT, RAW, DEBUG, MEDIA, COMMENTS):
    p.mkdir(parents=True, exist_ok=True)


def dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_name(value: str, limit: int = 90) -> str:
    value = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", value or "")
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return (value or "未命名")[:limit]


def fmt_ts(value: Any, fallback: str = "时间未知") -> str:
    try:
        number = int(value)
        if number > 10_000_000_000:
            number //= 1000
        return dt.datetime.fromtimestamp(number, TZ).strftime("%Y-%m-%d_%H-%M-%S")
    except Exception:
        return fallback


def walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def first_url(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    if isinstance(value, list):
        for item in value:
            result = first_url(item)
            if result:
                return result
    if isinstance(value, dict):
        for key in ("url_list", "download_url_list", "uri", "url"):
            if key in value:
                result = first_url(value[key])
                if result:
                    return result
    return None


def decode_embedded_json(html: str) -> list[Any]:
    found: list[Any] = []
    patterns = [
        r'<script[^>]+id="RENDER_DATA"[^>]*>(.*?)</script>',
        r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        r'<script[^>]+id="SIGI_STATE"[^>]*>(.*?)</script>',
        r'window\._ROUTER_DATA\s*=\s*({.*?})\s*;</script>',
    ]
    for pattern in patterns:
        for match in re.findall(pattern, html, flags=re.S | re.I):
            candidates = [match, urllib.parse.unquote(match)]
            for candidate in candidates:
                candidate = candidate.strip()
                try:
                    obj = json.loads(candidate)
                except Exception:
                    continue
                found.append(obj)
                break
    return found


def find_aweme_detail(datasets: list[Any]) -> dict[str, Any] | None:
    for dataset in datasets:
        for node in walk(dataset):
            if not isinstance(node, dict):
                continue
            direct = node.get("aweme_detail")
            if isinstance(direct, dict) and str(direct.get("aweme_id", "")) == AWEME_ID:
                return direct
            if str(node.get("aweme_id", "")) == AWEME_ID and (
                "author" in node or "video" in node or "images" in node
            ):
                return node
    return None


def collect_comments(datasets: list[Any]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for dataset in datasets:
        for node in walk(dataset):
            if not isinstance(node, dict):
                continue
            if "cid" in node and "user" in node and ("text" in node or "content" in node):
                cid = str(node.get("cid") or hashlib.sha256(json.dumps(node, sort_keys=True, default=str).encode()).hexdigest())
                result[cid] = node
    return list(result.values())


def comment_image_urls(comment: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    likely_keys = {
        "image_list",
        "image_comment",
        "image_url",
        "images",
        "comment_image",
        "comment_images",
    }
    for key, value in comment.items():
        if key in likely_keys or "image" in key.lower():
            if any(skip in key.lower() for skip in ("avatar", "icon", "badge", "emoji")):
                continue
            for node in walk(value):
                if isinstance(node, str) and node.startswith(("http://", "https://")):
                    urls.append(node)
                elif isinstance(node, dict):
                    u = first_url(node)
                    if u:
                        urls.append(u)
    # stable dedupe
    return list(dict.fromkeys(urls))


def is_author_comment(comment: dict[str, Any], author: dict[str, Any]) -> bool:
    user = comment.get("user") or {}
    author_ids = {
        str(author.get("uid") or ""),
        str(author.get("sec_uid") or ""),
        str(author.get("unique_id") or ""),
        str(author.get("short_id") or ""),
    } - {""}
    user_ids = {
        str(user.get("uid") or ""),
        str(user.get("sec_uid") or ""),
        str(user.get("unique_id") or ""),
        str(user.get("short_id") or ""),
    } - {""}
    explicit = any(
        bool(comment.get(key))
        for key in ("is_author", "is_creator", "author_is_commenter", "is_aweme_author")
    )
    labels = json.dumps(comment.get("label_list", []), ensure_ascii=False)
    return explicit or bool(author_ids & user_ids) or "作者" in labels


def response_interesting(url: str) -> bool:
    needles = (
        "/aweme/detail",
        "/aweme/v1/web/aweme/detail",
        "/comment/list",
        "/comment/list/reply",
        "/share/note/",
    )
    return any(x in url for x in needles)


async def save_page_snapshot(page: Page, name: str) -> None:
    try:
        await page.screenshot(path=str(DEBUG / f"{name}.png"), full_page=True)
    except Exception as exc:
        (DEBUG / f"{name}_screenshot_error.txt").write_text(str(exc), encoding="utf-8")
    try:
        (DEBUG / f"{name}.html").write_text(await page.content(), encoding="utf-8")
    except Exception as exc:
        (DEBUG / f"{name}_html_error.txt").write_text(str(exc), encoding="utf-8")


async def aggressively_scroll(page: Page, rounds: int = 100) -> None:
    for idx in range(rounds):
        try:
            await page.evaluate(
                """
                () => {
                  window.scrollTo(0, document.body.scrollHeight);
                  const all = [...document.querySelectorAll('*')];
                  for (const el of all) {
                    const s = getComputedStyle(el);
                    if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 100) {
                      el.scrollTop = el.scrollHeight;
                    }
                  }
                  for (const el of [...document.querySelectorAll('button,span,div')]) {
                    const t = (el.textContent || '').trim();
                    if (['展开更多', '查看全部', '更多回复', '展开'].includes(t) && el instanceof HTMLElement) {
                      try { el.click(); } catch (_) {}
                    }
                  }
                }
                """
            )
            await page.mouse.wheel(0, 2400)
        except Exception:
            pass
        await page.wait_for_timeout(850)
        if idx in (9, 29, 59, 99):
            await save_page_snapshot(page, f"scroll_{idx + 1:03d}")


async def browser_probe() -> tuple[list[Any], list[dict[str, Any]], list[str]]:
    datasets: list[Any] = []
    urls_seen: list[str] = []
    errors: list[str] = []
    pending: set[asyncio.Task[Any]] = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--lang=zh-CN",
                "--window-size=1440,1000",
            ],
        )
        context = await browser.new_context(
            user_agent=UA,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 1000},
            extra_http_headers={
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
                "Referer": "https://www.douyin.com/",
            },
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = await context.new_page()

        async def handle_response(response: Any) -> None:
            url = response.url
            if not response_interesting(url):
                return
            urls_seen.append(url)
            try:
                body = await response.body()
                suffix = ".json" if "json" in (response.headers.get("content-type") or "") else ".bin"
                idx = len(urls_seen)
                (RAW / f"response_{idx:04d}{suffix}").write_bytes(body)
                try:
                    datasets.append(json.loads(body))
                except Exception:
                    pass
            except Exception as exc:
                errors.append(f"response {url}: {exc}")

        def on_response(response: Any) -> None:
            task = asyncio.create_task(handle_response(response))
            pending.add(task)
            task.add_done_callback(pending.discard)

        page.on("response", on_response)

        targets = [SHORT_URL] + DIRECT_URLS
        for idx, target in enumerate(targets, start=1):
            try:
                await page.goto(target, wait_until="domcontentloaded", timeout=90000)
                await page.wait_for_timeout(12000)
                await save_page_snapshot(page, f"page_{idx:02d}_initial")
                html = await page.content()
                datasets.extend(decode_embedded_json(html))
                await aggressively_scroll(page)
                html = await page.content()
                datasets.extend(decode_embedded_json(html))
                await save_page_snapshot(page, f"page_{idx:02d}_final")
            except Exception as exc:
                errors.append(f"page {target}: {exc}")

        cookies = await context.cookies()
        dump_json(RAW / "browser_cookies.json", cookies)
        if pending:
            await asyncio.gather(*list(pending), return_exceptions=True)
        await browser.close()

    dump_json(RAW / "network_urls.json", urls_seen)
    dump_json(DEBUG / "browser_errors.json", errors)
    return datasets, cookies, errors


def requests_probe() -> list[Any]:
    datasets: list[Any] = []
    session = requests.Session()
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    targets = [SHORT_URL] + DIRECT_URLS
    records: list[dict[str, Any]] = []
    for idx, url in enumerate(targets, start=1):
        try:
            response = session.get(url, headers=headers, timeout=40, allow_redirects=True)
            (RAW / f"requests_{idx:02d}.html").write_bytes(response.content)
            records.append(
                {
                    "input": url,
                    "status": response.status_code,
                    "final_url": response.url,
                    "headers": dict(response.headers),
                }
            )
            datasets.extend(decode_embedded_json(response.text))
        except Exception as exc:
            records.append({"input": url, "error": str(exc)})
    dump_json(RAW / "requests_probe.json", records)
    return datasets


def try_ytdlp() -> None:
    commands = []
    for target in [SHORT_URL, DIRECT_URLS[0], DIRECT_URLS[1]]:
        commands.append(
            [
                sys.executable,
                "-m",
                "yt_dlp",
                "--no-playlist",
                "--write-info-json",
                "--write-thumbnail",
                "--no-overwrites",
                "--retries",
                "4",
                "--user-agent",
                UA,
                "-o",
                str(RAW / "ytdlp_%(id)s_%(title).80s.%(ext)s"),
                target,
            ]
        )
    logs = []
    for command in commands:
        proc = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        logs.append({"command": command, "returncode": proc.returncode, "output": proc.stdout})
        if proc.returncode == 0:
            break
    dump_json(DEBUG / "ytdlp_runs.json", logs)


def add_browser_cookies(session: requests.Session, cookies: list[dict[str, Any]]) -> None:
    for cookie in cookies:
        try:
            session.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain"))
        except Exception:
            pass


def download(session: requests.Session, url: str, path: Path, referer: str) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = {
        "User-Agent": UA,
        "Referer": referer,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    try:
        with session.get(url, headers=headers, stream=True, timeout=90, allow_redirects=True) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            with path.open("wb") as fh:
                for chunk in response.iter_content(1024 * 1024):
                    if chunk:
                        fh.write(chunk)
            return {
                "ok": True,
                "url": url,
                "path": str(path),
                "size": path.stat().st_size,
                "content_type": content_type,
                "final_url": response.url,
            }
    except Exception as exc:
        return {"ok": False, "url": url, "path": str(path), "error": str(exc)}


def media_from_aweme(aweme: dict[str, Any]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    video = aweme.get("video") or {}
    for key in ("play_addr", "download_addr", "play_addr_265", "play_addr_h264"):
        url = first_url(video.get(key))
        if url:
            result.append(("video", url))
            break
    for idx, image in enumerate(aweme.get("images") or [], start=1):
        url = first_url(image)
        if url:
            result.append((f"image_{idx:03d}", url))
    return result


def infer_ext(url: str, content_type: str = "", default: str = ".bin") -> str:
    ct = content_type.lower()
    if "video" in ct:
        return ".mp4"
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "png" in ct:
        return ".png"
    if "webp" in ct:
        return ".webp"
    path = urllib.parse.urlparse(url).path
    ext = Path(path).suffix.lower()
    if ext in {".mp4", ".mov", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"}:
        return ext
    return default


def organize(datasets: list[Any], cookies: list[dict[str, Any]]) -> Path:
    dump_json(RAW / "all_datasets.json", datasets)
    aweme = find_aweme_detail(datasets) or {}
    dump_json(ROOT / "作品原始信息.json", aweme)
    author = aweme.get("author") or {}
    title = aweme.get("desc") or aweme.get("preview_title") or f"抖音作品_{AWEME_ID}"
    work_time = fmt_ts(aweme.get("create_time"), "发布时间未知")
    author_name = author.get("nickname") or "作者未知"

    session = requests.Session()
    add_browser_cookies(session, cookies)

    downloads: list[dict[str, Any]] = []
    for index, (kind, url) in enumerate(media_from_aweme(aweme), start=1):
        default_ext = ".mp4" if kind == "video" else ".jpg"
        path = MEDIA / f"001_作品_{work_time}_{index:03d}_{safe_name(kind)}{default_ext}"
        record = download(session, url, path, DIRECT_URLS[0])
        if record.get("ok"):
            ext = infer_ext(url, record.get("content_type", ""), default_ext)
            if path.suffix != ext:
                renamed = path.with_suffix(ext)
                path.rename(renamed)
                record["path"] = str(renamed)
        downloads.append(record)

    all_comments = collect_comments(datasets)
    dump_json(ROOT / "抓取到的全部评论.json", all_comments)
    selected: list[dict[str, Any]] = []
    for comment in all_comments:
        urls = comment_image_urls(comment)
        if urls and is_author_comment(comment, author):
            selected.append(comment)

    comment_manifest: list[dict[str, Any]] = []
    for c_index, comment in enumerate(sorted(selected, key=lambda x: int(x.get("create_time") or 0)), start=2):
        ctime = fmt_ts(comment.get("create_time"), "评论时间未知")
        text = safe_name(comment.get("text") or comment.get("content") or "图片评论", 45)
        urls = comment_image_urls(comment)
        entry = {
            "编号": c_index,
            "评论ID": str(comment.get("cid") or ""),
            "发布时间": ctime,
            "评论文字": comment.get("text") or comment.get("content") or "",
            "图片数量": len(urls),
            "原始数据": comment,
            "下载": [],
        }
        for image_index, url in enumerate(urls, start=1):
            path = COMMENTS / f"{c_index:03d}_作者图片评论_{ctime}_{image_index:02d}_{text}.jpg"
            record = download(session, url, path, DIRECT_URLS[0])
            if record.get("ok"):
                ext = infer_ext(url, record.get("content_type", ""), ".jpg")
                if path.suffix != ext:
                    renamed = path.with_suffix(ext)
                    path.rename(renamed)
                    record["path"] = str(renamed)
            entry["下载"].append(record)
            downloads.append(record)
        comment_manifest.append(entry)

    dump_json(ROOT / "作者图片评论清单.json", comment_manifest)
    dump_json(ROOT / "下载结果.json", downloads)

    comment_times = [fmt_ts(c.get("create_time"), "") for c in selected if c.get("create_time")]
    if comment_times:
        range_name = comment_times[0] if len(comment_times) == 1 else f"{comment_times[0]}至{comment_times[-1]}"
    else:
        range_name = "评论时间未知"

    manifest_text = [
        "抖音公开作品存档",
        f"作品编号：001",
        f"作品ID：{AWEME_ID}",
        f"原分享链接：{SHORT_URL}",
        f"作品作者：{author_name}",
        f"作品发布时间：{work_time}",
        f"作品标题：{title}",
        f"抓取到的全部评论数：{len(all_comments)}",
        f"筛选出的作者图片评论数：{len(selected)}",
        "",
        "说明：仅筛选评论用户ID与作品作者ID一致、或接口明确标记为作者的图片评论。",
        "debug 目录保留网页快照、网络响应和错误信息，便于复核是否受登录或验证码限制。",
    ]
    (ROOT / "000_存档说明.txt").write_text("\n".join(manifest_text), encoding="utf-8")

    zip_name = safe_name(f"001_作品_{work_time}__作者图片评论_{range_name}_{AWEME_ID}", 180) + ".zip"
    zip_path = Path("artifact") / zip_name
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for file in sorted(ROOT.rglob("*")):
            if file.is_file():
                zf.write(file, file.relative_to(ROOT.parent))
    return zip_path


def main() -> int:
    try_ytdlp()
    datasets = requests_probe()
    browser_datasets, cookies, _errors = asyncio.run(browser_probe())
    datasets.extend(browser_datasets)
    zip_path = organize(datasets, cookies)
    print(f"ARCHIVE_PATH={zip_path}")
    print(f"ARCHIVE_SIZE={zip_path.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
