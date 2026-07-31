#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import re
import shutil
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any, Iterable

import requests
from playwright.async_api import async_playwright

AWEME_ID = "7666692683432429819"
SHORT_URL = "https://v.douyin.com/0Mybz_ScO3Q/"
DIRECT_URL = f"https://www.douyin.com/note/{AWEME_ID}"
AUTHOR_UID = "717344312142287"
TZ = dt.timezone(dt.timedelta(hours=8))
BASE = Path("browser_reply_source")
OUT = Path("browser_reply_output")
WORK_DIR = OUT / "001_作品内容"
COMMENT_DIR = OUT / "作者图片评论"
RAW_DIR = OUT / "原始数据"
DEBUG_DIR = OUT / "诊断"
ARTIFACT_DIR = Path("browser_reply_artifact")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/139.0.0.0 Safari/537.36"
)


def reset() -> None:
    for path in (BASE, OUT, ARTIFACT_DIR):
        if path.exists():
            shutil.rmtree(path)
    for path in (BASE, OUT, WORK_DIR, COMMENT_DIR, RAW_DIR, DEBUG_DIR, ARTIFACT_DIR):
        path.mkdir(parents=True, exist_ok=True)


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def safe(value: str, limit: int = 90) -> str:
    value = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", value or "")
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return (value or "未命名")[:limit]


def fmt_time(value: Any, fallback: str = "时间未知") -> str:
    try:
        ts = int(value)
        if ts > 10_000_000_000:
            ts //= 1000
        return dt.datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d_%H-%M-%S")
    except Exception:
        return fallback


def user_ids(user: dict[str, Any]) -> set[str]:
    keys = ("uid", "sec_uid", "secUid", "unique_id", "uniqueId", "short_id", "shortId")
    return {str(user.get(key)) for key in keys if user.get(key)}


def exact_author(comment: dict[str, Any], author: dict[str, Any]) -> bool:
    user = comment.get("user") if isinstance(comment.get("user"), dict) else {}
    return bool(user_ids(user) & user_ids(author))


def canonical_images(comment: dict[str, Any]) -> list[dict[str, str]]:
    images = comment.get("image_list")
    if not isinstance(images, list):
        return []
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for image in images:
        if not isinstance(image, dict):
            continue
        candidates: list[tuple[str, str, str]] = []
        for field in ("origin_url", "download_url", "medium_url", "crop_url", "thumb_url"):
            value = image.get(field)
            if not isinstance(value, dict):
                continue
            uri = str(value.get("uri") or "")
            urls = value.get("url_list") or value.get("urlList") or []
            if isinstance(urls, list):
                for url in urls:
                    if isinstance(url, str) and url.startswith(("http://", "https://")):
                        candidates.append((field, uri, url))
        if not candidates:
            continue
        identity = next((uri for _field, uri, _url in candidates if uri), candidates[0][2].split("~", 1)[0])
        if identity in seen:
            continue
        seen.add(identity)
        chosen = next((item for item in candidates if item[0] == "origin_url" and ".jpeg" in item[2]), None)
        chosen = chosen or next((item for item in candidates if item[0] == "origin_url"), None)
        chosen = chosen or next((item for item in candidates if "thumb" not in item[2] and "watermark" not in item[2]), None)
        chosen = chosen or candidates[0]
        output.append({"identity": identity, "field": chosen[0], "url": chosen[2]})
    return output


def unique_comments(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("cid") or item.get("comment_id") or hashlib.sha256(
            json.dumps(item, sort_keys=True, default=str).encode()
        ).hexdigest())
        if cid in seen:
            continue
        seen.add(cid)
        output.append(item)
    return output


def flatten(items: Iterable[dict[str, Any]], parent_id: str | None = None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source in items:
        if not isinstance(source, dict):
            continue
        item = dict(source)
        cid = str(item.get("cid") or item.get("comment_id") or "")
        if parent_id:
            item["_level"] = 2
            item["_parent_comment_id"] = parent_id
        else:
            item.setdefault("_level", 1)
        result.append(item)
        nested: list[dict[str, Any]] = []
        for key in ("reply_comment", "comments", "replies", "reply_comments"):
            value = item.get(key)
            if isinstance(value, list):
                nested.extend(child for child in value if isinstance(child, dict))
        if nested:
            result.extend(flatten(nested, cid or parent_id))
    return result


def extract_strict_source() -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    parts = sorted(Path("strict_published").glob("archive.part.*"))
    if not parts:
        raise RuntimeError("严格抓取源包不存在")
    joined = BASE / "strict_source.zip"
    with joined.open("wb") as output:
        for part in parts:
            output.write(part.read_bytes())
    extracted = BASE / "extracted"
    with zipfile.ZipFile(joined) as archive:
        archive.extractall(extracted)
    source_root = extracted / "hybrid_output"
    detail = json.loads((source_root / "原始数据" / "001_作品完整信息.json").read_text("utf-8"))
    comments_path = source_root / "原始数据" / "全部一级评论及二级回复.json"
    comments = json.loads(comments_path.read_text("utf-8"))
    for file in (source_root / "001_作品内容").glob("*"):
        if file.is_file():
            shutil.copy2(file, WORK_DIR / file.name)
    return source_root, detail, comments


async def capture_replies() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    response_records: list[dict[str, Any]] = []
    reply_payloads: list[dict[str, Any]] = []
    click_log: list[dict[str, Any]] = []
    pending: set[asyncio.Task[Any]] = set()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
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
            extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9"},
        )
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        page = await context.new_page()

        async def save_response(response: Any) -> None:
            if "/aweme/v1/web/comment/list/reply/" not in response.url:
                return
            record: dict[str, Any] = {"url": response.url, "status": response.status}
            try:
                payload = await response.json()
                record["keys"] = sorted(payload.keys()) if isinstance(payload, dict) else []
                record["comment_count"] = len(payload.get("comments") or []) if isinstance(payload, dict) else 0
                record["status_code"] = payload.get("status_code") if isinstance(payload, dict) else None
                record["status_msg"] = payload.get("status_msg") if isinstance(payload, dict) else None
                if isinstance(payload, dict):
                    reply_payloads.append(payload)
            except Exception as exc:
                record["error"] = repr(exc)
            response_records.append(record)

        def on_response(response: Any) -> None:
            task = asyncio.create_task(save_response(response))
            pending.add(task)
            task.add_done_callback(pending.discard)

        page.on("response", on_response)
        await page.goto(DIRECT_URL, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(12000)

        for phase in ("down", "up", "down-final"):
            rounds = 115 if phase == "down" else 75 if phase == "up" else 55
            for index in range(rounds):
                clicked = await page.evaluate(
                    """
                    () => {
                      const regex = /(展开\s*\d+\s*条回复|查看\s*\d+\s*条回复|展开更多回复|查看更多回复|更多回复)/;
                      const buttons = [...document.querySelectorAll('button')];
                      let count = 0;
                      const texts = [];
                      for (const button of buttons) {
                        const text = (button.innerText || button.textContent || '').replace(/\s+/g, ' ').trim();
                        const rect = button.getBoundingClientRect();
                        if (!regex.test(text) || rect.width < 2 || rect.height < 2) continue;
                        try {
                          button.scrollIntoView({block: 'center'});
                          button.click();
                          count += 1;
                          texts.push(text);
                        } catch (_) {}
                      }
                      return {count, texts};
                    }
                    """
                )
                if clicked.get("count"):
                    click_log.append({"phase": phase, "round": index, **clicked})
                    await page.wait_for_timeout(1200)

                direction = -1 if phase == "up" else 1
                await page.evaluate(
                    """
                    (direction) => {
                      window.scrollBy(0, direction * Math.max(700, innerHeight * 0.85));
                      for (const el of [...document.querySelectorAll('*')]) {
                        const style = getComputedStyle(el);
                        if ((style.overflowY === 'auto' || style.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 100) {
                          el.scrollTop += direction * Math.max(500, el.clientHeight * 0.8);
                        }
                      }
                    }
                    """,
                    direction,
                )
                await page.mouse.wheel(0, direction * 1800)
                await page.wait_for_timeout(500)

            if phase == "down":
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            elif phase == "up":
                await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(2500)

        await page.screenshot(path=str(DEBUG_DIR / "评论区最终页面.png"), full_page=True)
        (DEBUG_DIR / "评论区最终页面.html").write_text(await page.content(), encoding="utf-8")
        browser_cookies = await context.cookies()
        if pending:
            await asyncio.gather(*list(pending), return_exceptions=True)
        await browser.close()

    # Cookies are returned only in memory for media downloads and are never written into the archive.
    return reply_payloads, response_records, click_log, browser_cookies


def infer_ext(url: str, content_type: str, default: str = ".jpg") -> str:
    lower = content_type.lower()
    if "jpeg" in lower or "jpg" in lower:
        return ".jpg"
    if "png" in lower:
        return ".png"
    if "webp" in lower:
        return ".webp"
    if "heic" in lower or "heif" in lower:
        return ".heic"
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp", ".heic"} else default


def download_image(session: requests.Session, url: str, target: Path) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with session.get(
            url,
            headers={"User-Agent": UA, "Referer": DIRECT_URL, "Accept": "image/avif,image/webp,image/*,*/*"},
            timeout=90,
            stream=True,
            allow_redirects=True,
        ) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            with target.open("wb") as output:
                for chunk in response.iter_content(1024 * 1024):
                    if chunk:
                        output.write(chunk)
        extension = infer_ext(url, content_type)
        if target.suffix.lower() != extension:
            renamed = target.with_suffix(extension)
            target.rename(renamed)
            target = renamed
        return {"ok": True, "url": url, "file": str(target), "size": target.stat().st_size, "content_type": content_type}
    except Exception as exc:
        return {"ok": False, "url": url, "file": str(target), "error": repr(exc)}


def package(detail: dict[str, Any], base_comments: list[dict[str, Any]], reply_payloads: list[dict[str, Any]], browser_cookies: list[dict[str, Any]], response_records: list[dict[str, Any]], click_log: list[dict[str, Any]]) -> Path:
    author = detail.get("authorInfo") if isinstance(detail.get("authorInfo"), dict) else {"uid": AUTHOR_UID}
    merged: list[dict[str, Any]] = list(flatten(base_comments))
    raw_replies: list[dict[str, Any]] = []
    for payload in reply_payloads:
        replies = [item for item in (payload.get("comments") or []) if isinstance(item, dict)]
        raw_replies.extend(replies)
        merged.extend(flatten(replies))
    merged = unique_comments(merged)

    dump(RAW_DIR / "001_作品完整信息.json", detail)
    dump(RAW_DIR / "全部已取得评论及回复.json", merged)
    dump(RAW_DIR / "浏览器展开取得的回复.json", unique_comments(flatten(raw_replies)))
    dump(DEBUG_DIR / "回复接口响应清单.json", response_records)
    dump(DEBUG_DIR / "展开按钮点击记录.json", click_log)

    session = requests.Session()
    for cookie in browser_cookies:
        try:
            session.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain"))
        except Exception:
            pass

    selected = [item for item in merged if exact_author(item, author) and canonical_images(item)]
    selected.sort(key=lambda item: int(item.get("create_time") or 0))
    manifest: list[dict[str, Any]] = []
    downloads: list[dict[str, Any]] = []
    for number, comment in enumerate(selected, start=2):
        comment_time = fmt_time(comment.get("create_time"), "评论时间未知")
        text = str(comment.get("text") or "图片评论")
        images = canonical_images(comment)
        entry = {
            "编号": number,
            "评论ID": str(comment.get("cid") or ""),
            "层级": int(comment.get("_level") or comment.get("level") or 1),
            "父评论ID": comment.get("_parent_comment_id"),
            "发布时间": comment_time,
            "评论文字": text,
            "图片数量": len(images),
            "下载": [],
            "原始数据": comment,
        }
        for image_index, image in enumerate(images, start=1):
            target = COMMENT_DIR / f"{number:03d}_{comment_time}_{image_index:02d}_{safe(text, 48)}.jpg"
            result = download_image(session, image["url"], target)
            result["identity"] = image["identity"]
            result["source_field"] = image["field"]
            entry["下载"].append(result)
            downloads.append(result)
        manifest.append(entry)

    dump(OUT / "作者图片评论清单.json", manifest)
    dump(OUT / "图片下载结果.json", downloads)

    top_level = unique_comments(item for item in merged if int(item.get("_level") or item.get("level") or 1) == 1)
    replies = unique_comments(item for item in merged if int(item.get("_level") or item.get("level") or 1) == 2)
    expected_replies = sum(int(item.get("reply_comment_total") or 0) for item in top_level)
    work_time = fmt_time(detail.get("createTime"), "作品时间未知")
    times = [item["发布时间"] for item in manifest]
    time_range = "无作者图片评论" if not times else times[0] if len(times) == 1 else f"{times[0]}至{times[-1]}"
    successful_reply_responses = sum(1 for record in response_records if int(record.get("comment_count") or 0) > 0)
    summary = [
        "抖音公开作品及评论区证据存档",
        "编号：001",
        f"作品ID：{AWEME_ID}",
        f"原分享链接：{SHORT_URL}",
        f"作者：{author.get('nickname') or 'REASON'}",
        f"作者UID：{next(iter(user_ids(author)), AUTHOR_UID)}",
        f"作品发布时间：{work_time}",
        f"作品主体文件数：{len(list(WORK_DIR.glob('*')))}",
        f"去重后一级评论数：{len(top_level)}",
        f"一级评论声明的二级回复总数：{expected_replies}",
        f"实际取得二级回复数：{len(replies)}",
        f"成功返回内容的回复接口响应数：{successful_reply_responses}",
        f"作者本人发布的图片评论数：{len(manifest)}",
        "作者判定：评论用户 UID/sec_uid 与作品作者完全一致。",
        "“作者赞过”标签不等于作者本人发布，已排除。",
        "浏览器登录态 Cookie 未写入此压缩包。",
    ]
    (OUT / "000_存档说明.txt").write_text("\n".join(summary), encoding="utf-8")

    archive_name = safe(f"001_作品_{work_time}__作者图片评论_{time_range}_{AWEME_ID}", 190) + ".zip"
    archive_path = ARTIFACT_DIR / archive_name
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file in sorted(OUT.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(OUT.parent))
    return archive_path


def main() -> int:
    reset()
    _source_root, detail, base_comments = extract_strict_source()
    reply_payloads, response_records, click_log, browser_cookies = asyncio.run(capture_replies())
    archive = package(detail, base_comments, reply_payloads, browser_cookies, response_records, click_log)
    print(f"ARCHIVE_PATH={archive}")
    print(f"ARCHIVE_SIZE={archive.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
