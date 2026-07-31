#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import re
import sys
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any, Iterable

import requests

AWEME_ID = "7666692683432429819"
SHORT_URL = "https://v.douyin.com/0Mybz_ScO3Q/"
TZ = dt.timezone(dt.timedelta(hours=8))
ROOT = Path("hybrid_output")
MEDIA_DIR = ROOT / "001_作品内容"
COMMENT_DIR = ROOT / "作者图片评论"
RAW_DIR = ROOT / "原始数据"
DEBUG_DIR = ROOT / "诊断"
ARTIFACT_DIR = Path("hybrid_artifact")
SOURCE_DIR = Path("hybrid_source")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/139.0.0.0 Safari/537.36"
)


def reset_output() -> None:
    import shutil

    for path in (ROOT, ARTIFACT_DIR, SOURCE_DIR):
        if path.exists():
            shutil.rmtree(path)
    for path in (ROOT, MEDIA_DIR, COMMENT_DIR, RAW_DIR, DEBUG_DIR, ARTIFACT_DIR, SOURCE_DIR):
        path.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def safe_name(value: str, limit: int = 90) -> str:
    value = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", value or "")
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return (value or "未命名")[:limit]


def format_time(value: Any, fallback: str = "时间未知") -> str:
    try:
        timestamp = int(value)
        if timestamp > 10_000_000_000:
            timestamp //= 1000
        return dt.datetime.fromtimestamp(timestamp, TZ).strftime("%Y-%m-%d_%H-%M-%S")
    except Exception:
        return fallback


def first_url(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    if isinstance(value, list):
        for child in value:
            result = first_url(child)
            if result:
                return result
    if isinstance(value, dict):
        for key in ("urlList", "url_list", "downloadUrlList", "download_url_list", "url", "uri"):
            if key in value:
                result = first_url(value[key])
                if result:
                    return result
    return None


def user_ids(user: dict[str, Any]) -> set[str]:
    keys = ("uid", "sec_uid", "secUid", "unique_id", "uniqueId", "short_id", "shortId")
    return {str(user.get(key)) for key in keys if user.get(key)}


def is_exact_author(comment: dict[str, Any], author: dict[str, Any]) -> bool:
    user = comment.get("user") if isinstance(comment.get("user"), dict) else {}
    return bool(user_ids(user) & user_ids(author))


def canonical_comment_images(comment: dict[str, Any]) -> list[dict[str, str]]:
    images = comment.get("image_list")
    if not isinstance(images, list):
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for image in images:
        if not isinstance(image, dict):
            continue
        candidates = []
        for key in ("origin_url", "download_url", "medium_url", "crop_url", "thumb_url"):
            item = image.get(key)
            if not isinstance(item, dict):
                continue
            uri = str(item.get("uri") or "")
            urls = item.get("url_list") or item.get("urlList") or []
            if isinstance(urls, list):
                for url in urls:
                    if isinstance(url, str) and url.startswith(("http://", "https://")):
                        candidates.append((key, uri, url))
        if not candidates:
            continue
        identity = next((uri for _key, uri, _url in candidates if uri), candidates[0][2].split("~", 1)[0])
        if identity in seen:
            continue
        seen.add(identity)
        # Prefer full-resolution JPEG from origin_url, then any origin_url, avoiding watermark/thumbnail.
        chosen = next((x for x in candidates if x[0] == "origin_url" and ".jpeg" in x[2]), None)
        chosen = chosen or next((x for x in candidates if x[0] == "origin_url"), None)
        chosen = chosen or next((x for x in candidates if "watermark" not in x[2] and "thumb" not in x[2]), None)
        chosen = chosen or candidates[0]
        result.append({"identity": identity, "url": chosen[2], "source_field": chosen[0]})
    return result


def parse_saved_page() -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    parts = sorted(Path("published").glob("archive.part.*"))
    if not parts:
        raise RuntimeError("缺少第一轮抓取包分片")
    source_zip = SOURCE_DIR / "source.zip"
    with source_zip.open("wb") as output:
        for part in parts:
            output.write(part.read_bytes())
    with zipfile.ZipFile(source_zip) as archive:
        archive.extractall(SOURCE_DIR / "extracted")

    source_root = SOURCE_DIR / "extracted" / "douyin_output"
    html = (source_root / "debug" / "page_02_final.html").read_text("utf-8", errors="ignore")
    detail: dict[str, Any] | None = None
    first_page: list[dict[str, Any]] = []
    for match in re.finditer(r'self\.__pace_f\.push\((\[.*?\])\)</script>', html, re.S):
        try:
            chunk = json.loads(match.group(1))
            if len(chunk) < 2 or not isinstance(chunk[1], str) or not chunk[1].startswith("7:"):
                continue
            page_data = json.loads(chunk[1][2:])
            props = page_data[3] if isinstance(page_data, list) and len(page_data) > 3 else {}
            candidate = ((props.get("aweme") or {}).get("detail")) if isinstance(props, dict) else None
            if isinstance(candidate, dict) and str(candidate.get("awemeId")) == AWEME_ID:
                detail = candidate
                comment_state = props.get("comment") if isinstance(props.get("comment"), dict) else {}
                first_page = [item for item in (comment_state.get("comments") or []) if isinstance(item, dict)]
                break
        except Exception:
            continue
    if not detail:
        raise RuntimeError("无法从保存页面解析作品详情")

    cookies = json.loads((source_root / "raw" / "browser_cookies.json").read_text("utf-8"))
    return detail, first_page, cookies


def unique_comments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        cid = str(item.get("cid") or item.get("comment_id") or hashlib.sha256(
            json.dumps(item, sort_keys=True, default=str).encode()
        ).hexdigest())
        if cid in seen:
            continue
        seen.add(cid)
        output.append(item)
    return output


async def collect_all_comments(cookies: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    vendor = Path("vendor/douyin-downloader").resolve()
    sys.path.insert(0, str(vendor))
    from core.api_client import DouyinAPIClient  # type: ignore

    cookie_map = {
        str(cookie.get("name")): str(cookie.get("value"))
        for cookie in cookies
        if cookie.get("name") and cookie.get("value")
    }
    diagnostics: dict[str, Any] = {
        "cookie_names": sorted(cookie_map),
        "top_level_pages": [],
        "reply_pages": [],
    }
    top_level: list[dict[str, Any]] = []

    async with DouyinAPIClient(cookie_map) as client:
        cursor = 0
        while True:
            page = await client.get_aweme_comments(AWEME_ID, cursor=cursor, count=20, include_replies=False)
            raw = page.get("raw") if isinstance(page.get("raw"), dict) else {}
            items = [item for item in (page.get("items") or []) if isinstance(item, dict)]
            diagnostics["top_level_pages"].append({
                "cursor": cursor,
                "count": len(items),
                "status_code": page.get("status_code"),
                "status_msg": raw.get("status_msg"),
                "has_more": page.get("has_more"),
                "next_cursor": page.get("max_cursor"),
                "risk_flags": page.get("risk_flags"),
            })
            top_level.extend(items)
            if not page.get("has_more") or not items:
                break
            next_cursor = int(page.get("max_cursor") or 0)
            if next_cursor == cursor:
                break
            cursor = next_cursor
            await asyncio.sleep(0.12)

        top_level = unique_comments(top_level)
        all_items: list[dict[str, Any]] = []
        for parent_index, parent in enumerate(top_level, start=1):
            parent = dict(parent)
            parent["_level"] = 1
            all_items.append(parent)
            parent_id = str(parent.get("cid") or parent.get("comment_id") or "")
            expected = int(parent.get("reply_comment_total") or 0)

            embedded = [item for item in (parent.get("reply_comment") or []) if isinstance(item, dict)]
            replies: list[dict[str, Any]] = list(embedded)
            reply_cursor = 0
            if expected > len(embedded):
                while True:
                    params = await client._default_query()
                    params.update({
                        "comment_id": parent_id,
                        "cursor": reply_cursor,
                        "count": 20,
                        "item_type": 0,
                        "item_id": AWEME_ID,
                    })
                    raw_reply = await client._request_json(
                        "/aweme/v1/web/comment/list/reply/",
                        params,
                        request_headers={
                            "Referer": f"https://www.douyin.com/note/{AWEME_ID}",
                            "Origin": "https://www.douyin.com",
                        },
                    )
                    page_replies = [item for item in (raw_reply.get("comments") or []) if isinstance(item, dict)]
                    has_more = bool(int(raw_reply.get("has_more") or 0))
                    next_cursor = int(raw_reply.get("cursor") or raw_reply.get("max_cursor") or 0)
                    diagnostics["reply_pages"].append({
                        "parent_index": parent_index,
                        "comment_id": parent_id,
                        "expected_total": expected,
                        "embedded_count": len(embedded),
                        "cursor": reply_cursor,
                        "count": len(page_replies),
                        "status_code": raw_reply.get("status_code"),
                        "status_msg": raw_reply.get("status_msg"),
                        "has_more": has_more,
                        "next_cursor": next_cursor,
                    })
                    replies.extend(page_replies)
                    if not has_more or not page_replies or next_cursor == reply_cursor:
                        break
                    reply_cursor = next_cursor
                    await asyncio.sleep(0.12)

            for reply in unique_comments(replies):
                reply = dict(reply)
                reply["_level"] = 2
                reply["_parent_comment_id"] = parent_id
                all_items.append(reply)

    return unique_comments(all_items), diagnostics


def infer_extension(url: str, content_type: str, default: str) -> str:
    content_type = content_type.lower()
    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"
    if "heic" in content_type or "heif" in content_type:
        return ".heic"
    if "video" in content_type:
        return ".mp4"
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp", ".heic", ".mp4"} else default


def download(session: requests.Session, url: str, target: Path) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    headers = {
        "User-Agent": UA,
        "Referer": f"https://www.douyin.com/note/{AWEME_ID}",
        "Accept": "*/*",
    }
    try:
        with session.get(url, headers=headers, timeout=90, stream=True, allow_redirects=True) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            with target.open("wb") as output:
                for chunk in response.iter_content(1024 * 1024):
                    if chunk:
                        output.write(chunk)
        extension = infer_extension(url, content_type, target.suffix or ".bin")
        if target.suffix.lower() != extension:
            renamed = target.with_suffix(extension)
            target.rename(renamed)
            target = renamed
        return {
            "ok": True,
            "url": url,
            "file": str(target),
            "size": target.stat().st_size,
            "content_type": content_type,
        }
    except Exception as exc:
        return {"ok": False, "url": url, "file": str(target), "error": str(exc)}


def work_media(detail: dict[str, Any]) -> list[tuple[str, str, str]]:
    result: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for index, image in enumerate(detail.get("images") or [], start=1):
        if not isinstance(image, dict):
            continue
        url = first_url(image.get("urlList") or image.get("url_list") or image)
        if url and url not in seen:
            seen.add(url)
            result.append((f"图片_{index:03d}", url, ".webp"))
        live_url = first_url(image.get("video"))
        if live_url and live_url not in seen:
            seen.add(live_url)
            result.append((f"实况视频_{index:03d}", live_url, ".mp4"))
    if not result:
        video = detail.get("video") if isinstance(detail.get("video"), dict) else {}
        url = first_url(video.get("playAddr") or video.get("play_addr"))
        if url:
            result.append(("视频", url, ".mp4"))
    return result


def build_archive(detail: dict[str, Any], comments: list[dict[str, Any]], diagnostics: dict[str, Any]) -> Path:
    write_json(RAW_DIR / "001_作品完整信息.json", detail)
    write_json(RAW_DIR / "全部一级评论及二级回复.json", comments)
    write_json(DEBUG_DIR / "评论分页诊断.json", diagnostics)

    author = detail.get("authorInfo") if isinstance(detail.get("authorInfo"), dict) else {}
    work_time = format_time(detail.get("createTime"), "作品时间未知")
    title = str(detail.get("desc") or f"作品_{AWEME_ID}")
    session = requests.Session()
    download_results: list[dict[str, Any]] = []

    media = work_media(detail)
    for index, (kind, url, default_extension) in enumerate(media, start=1):
        target = MEDIA_DIR / f"001_{work_time}_{index:03d}_{safe_name(kind)}{default_extension}"
        download_results.append(download(session, url, target))

    author_image_comments = [
        comment for comment in comments
        if is_exact_author(comment, author) and canonical_comment_images(comment)
    ]
    author_image_comments.sort(key=lambda item: int(item.get("create_time") or item.get("createTime") or 0))

    manifest: list[dict[str, Any]] = []
    for number, comment in enumerate(author_image_comments, start=2):
        comment_time = format_time(comment.get("create_time") or comment.get("createTime"), "评论时间未知")
        text = str(comment.get("text") or comment.get("content") or "图片评论")
        images = canonical_comment_images(comment)
        entry = {
            "编号": number,
            "评论ID": str(comment.get("cid") or comment.get("comment_id") or ""),
            "层级": int(comment.get("_level") or comment.get("level") or 1),
            "父评论ID": comment.get("_parent_comment_id"),
            "发布时间": comment_time,
            "评论文字": text,
            "图片数量": len(images),
            "下载": [],
            "原始数据": comment,
        }
        for image_index, image in enumerate(images, start=1):
            target = COMMENT_DIR / f"{number:03d}_{comment_time}_{image_index:02d}_{safe_name(text, 48)}.jpg"
            result = download(session, image["url"], target)
            result["identity"] = image["identity"]
            result["source_field"] = image["source_field"]
            entry["下载"].append(result)
            download_results.append(result)
        manifest.append(entry)

    write_json(ROOT / "作者图片评论清单.json", manifest)
    write_json(ROOT / "下载结果.json", download_results)

    reply_expected = sum(int(item.get("reply_comment_total") or 0) for item in comments if int(item.get("_level") or 1) == 1)
    top_count = sum(1 for item in comments if int(item.get("_level") or 1) == 1)
    reply_count = sum(1 for item in comments if int(item.get("_level") or 1) == 2)
    comment_times = [item["发布时间"] for item in manifest]
    comment_range = "无作者图片评论" if not comment_times else (
        comment_times[0] if len(comment_times) == 1 else f"{comment_times[0]}至{comment_times[-1]}"
    )

    summary = [
        "抖音公开作品存档",
        "编号：001",
        f"作品ID：{AWEME_ID}",
        f"原分享链接：{SHORT_URL}",
        f"作者：{author.get('nickname') or '作者未知'}",
        f"作者UID：{author.get('uid') or detail.get('authorUserId')}",
        f"作品发布时间：{work_time}",
        f"标题：{title}",
        f"作品媒体文件数：{len(media)}",
        f"去重后一级评论数：{top_count}",
        f"实际取得二级回复数：{reply_count}",
        f"一级评论声明的二级回复总数：{reply_expected}",
        f"作者本人发布的图片评论数：{len(manifest)}",
        "作者判定规则：评论用户 UID/sec_uid 必须与作品作者完全一致。",
        "“作者赞过”仅代表作品作者点赞过该评论，不会被误判成作者本人评论。",
    ]
    (ROOT / "000_存档说明.txt").write_text("\n".join(summary), encoding="utf-8")

    archive_name = safe_name(
        f"001_作品_{work_time}__作者图片评论_{comment_range}_{AWEME_ID}", 190
    ) + ".zip"
    archive_path = ARTIFACT_DIR / archive_name
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file in sorted(ROOT.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(ROOT.parent))
    return archive_path


def main() -> int:
    reset_output()
    detail, first_page, cookies = parse_saved_page()
    write_json(RAW_DIR / "页面首屏评论.json", first_page)
    comments, diagnostics = asyncio.run(collect_all_comments(cookies))
    archive = build_archive(detail, comments, diagnostics)
    print(f"ARCHIVE_PATH={archive}")
    print(f"ARCHIVE_SIZE={archive.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
