#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import re
import shutil
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
MEDIA = ROOT / "001_作品内容"
COMMENT_MEDIA = ROOT / "作者图片评论"
RAW = ROOT / "原始数据"
DEBUG = ROOT / "诊断"
ARTIFACT = Path("hybrid_artifact")
SOURCE = Path("hybrid_source")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/139.0.0.0 Safari/537.36"
)

for p in (ROOT, MEDIA, COMMENT_MEDIA, RAW, DEBUG, ARTIFACT, SOURCE):
    p.mkdir(parents=True, exist_ok=True)


def dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for v in value.values():
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)


def safe(value: str, limit: int = 90) -> str:
    value = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", value or "")
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return (value or "未命名")[:limit]


def fmt_ts(value: Any, fallback: str = "时间未知") -> str:
    try:
        ts = int(value)
        if ts > 10_000_000_000:
            ts //= 1000
        return dt.datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d_%H-%M-%S")
    except Exception:
        return fallback


def first_url(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    if isinstance(value, list):
        for item in value:
            result = first_url(item)
            if result:
                return result
    if isinstance(value, dict):
        for key in ("urlList", "url_list", "downloadUrlList", "download_url_list", "url", "uri"):
            if key in value:
                result = first_url(value[key])
                if result:
                    return result
    return None


def all_urls(value: Any) -> list[str]:
    result: list[str] = []
    for node in walk(value):
        if isinstance(node, str) and node.startswith(("http://", "https://")):
            result.append(node)
    return list(dict.fromkeys(result))


def ids(user: dict[str, Any]) -> set[str]:
    return {
        str(user.get("uid") or ""),
        str(user.get("sec_uid") or user.get("secUid") or ""),
        str(user.get("unique_id") or user.get("uniqueId") or ""),
        str(user.get("short_id") or user.get("shortId") or ""),
    } - {""}


def comment_images(comment: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for key, value in comment.items():
        lower = key.lower()
        if "image" not in lower and key not in {"pictures", "photo_list"}:
            continue
        if any(s in lower for s in ("avatar", "icon", "emoji", "badge")):
            continue
        result.extend(all_urls(value))
    return list(dict.fromkeys(result))


def is_author(comment: dict[str, Any], author: dict[str, Any]) -> bool:
    user = comment.get("user") if isinstance(comment.get("user"), dict) else {}
    explicit = any(bool(comment.get(k)) for k in ("is_author", "is_creator", "is_aweme_author"))
    label = str(comment.get("label_text") or "") + json.dumps(comment.get("label_list") or [], ensure_ascii=False)
    return explicit or bool(ids(user) & ids(author)) or "作者" in label


def parse_source_bundle() -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    parts = sorted(Path("published").glob("archive.part.*"))
    if not parts:
        raise RuntimeError("existing source archive parts are missing")
    joined = SOURCE / "source.zip"
    with joined.open("wb") as out:
        for part in parts:
            out.write(part.read_bytes())
    with zipfile.ZipFile(joined) as zf:
        zf.extractall(SOURCE / "extracted")

    src_root = SOURCE / "extracted" / "douyin_output"
    html_path = src_root / "debug" / "page_02_final.html"
    html = html_path.read_text("utf-8", errors="ignore")
    chunks: list[Any] = []
    for match in re.finditer(r'self\.__pace_f\.push\((\[.*?\])\)</script>', html, re.S):
        try:
            chunks.append(json.loads(match.group(1)))
        except Exception:
            continue
    detail: dict[str, Any] | None = None
    initial_comments: list[dict[str, Any]] = []
    for chunk in chunks:
        if len(chunk) < 2 or not isinstance(chunk[1], str) or not chunk[1].startswith("7:"):
            continue
        try:
            page = json.loads(chunk[1][2:])
        except Exception:
            continue
        props = page[3] if isinstance(page, list) and len(page) > 3 and isinstance(page[3], dict) else {}
        candidate = ((props.get("aweme") or {}).get("detail"))
        if isinstance(candidate, dict) and str(candidate.get("awemeId")) == AWEME_ID:
            detail = candidate
            comment_obj = props.get("comment") if isinstance(props.get("comment"), dict) else {}
            initial_comments = [x for x in (comment_obj.get("comments") or []) if isinstance(x, dict)]
            break
    if not detail:
        raise RuntimeError("failed to parse complete work detail from saved page")

    cookies_path = src_root / "raw" / "browser_cookies.json"
    cookies = json.loads(cookies_path.read_text("utf-8"))
    return detail, initial_comments, cookies


async def fetch_all_comments(cookies_list: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    vendor = Path("vendor/douyin-downloader").resolve()
    sys.path.insert(0, str(vendor))
    from core.api_client import DouyinAPIClient  # type: ignore

    cookie_map = {
        str(c.get("name")): str(c.get("value"))
        for c in cookies_list
        if c.get("name") and c.get("value")
    }
    diagnostics: dict[str, Any] = {"cookie_names": sorted(cookie_map), "pages": [], "reply_pages": []}
    comments: list[dict[str, Any]] = []
    seen: set[str] = set()

    async with DouyinAPIClient(cookie_map) as client:
        cursor = 0
        while True:
            page = await client.get_aweme_comments(AWEME_ID, cursor=cursor, count=20, include_replies=False)
            raw = page.get("raw") if isinstance(page.get("raw"), dict) else {}
            items = page.get("items") or []
            diagnostics["pages"].append({
                "cursor": cursor,
                "count": len(items),
                "status_code": page.get("status_code"),
                "status_msg": raw.get("status_msg"),
                "has_more": page.get("has_more"),
                "next_cursor": page.get("max_cursor"),
                "risk_flags": page.get("risk_flags"),
            })
            for item in items:
                if not isinstance(item, dict):
                    continue
                cid = str(item.get("cid") or item.get("comment_id") or hashlib.sha256(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest())
                if cid not in seen:
                    seen.add(cid)
                    item["_level"] = 1
                    comments.append(item)

                reply_total = int(item.get("reply_comment_total") or 0)
                if reply_total <= 0:
                    continue
                r_cursor = 0
                while True:
                    rpage = await client.get_aweme_comment_replies(
                        aweme_id=AWEME_ID,
                        comment_id=cid,
                        cursor=r_cursor,
                        count=20,
                    )
                    rraw = rpage.get("raw") if isinstance(rpage.get("raw"), dict) else {}
                    replies = rpage.get("items") or []
                    diagnostics["reply_pages"].append({
                        "comment_id": cid,
                        "cursor": r_cursor,
                        "count": len(replies),
                        "status_code": rpage.get("status_code"),
                        "status_msg": rraw.get("status_msg"),
                        "has_more": rpage.get("has_more"),
                        "next_cursor": rpage.get("max_cursor"),
                    })
                    for reply in replies:
                        if not isinstance(reply, dict):
                            continue
                        rid = str(reply.get("cid") or reply.get("comment_id") or hashlib.sha256(json.dumps(reply, sort_keys=True, default=str).encode()).hexdigest())
                        if rid not in seen:
                            seen.add(rid)
                            reply["_level"] = 2
                            reply["_parent_comment_id"] = cid
                            comments.append(reply)
                    if not rpage.get("has_more") or not replies:
                        break
                    nxt = int(rpage.get("max_cursor") or 0)
                    if nxt == r_cursor:
                        break
                    r_cursor = nxt
                    await asyncio.sleep(0.1)

            if not page.get("has_more") or not items:
                break
            nxt = int(page.get("max_cursor") or 0)
            if nxt == cursor:
                break
            cursor = nxt
            await asyncio.sleep(0.1)
    return comments, diagnostics


def infer_ext(url: str, content_type: str, default: str) -> str:
    ct = content_type.lower()
    if "video" in ct:
        return ".mp4"
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "png" in ct:
        return ".png"
    if "webp" in ct:
        return ".webp"
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    return suffix if suffix in {".mp4", ".mov", ".jpg", ".jpeg", ".png", ".webp", ".gif"} else default


def download(session: requests.Session, url: str, target: Path) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": UA, "Referer": f"https://www.douyin.com/note/{AWEME_ID}", "Accept": "*/*"}
    try:
        with session.get(url, headers=headers, timeout=90, stream=True, allow_redirects=True) as response:
            response.raise_for_status()
            ctype = response.headers.get("content-type", "")
            with target.open("wb") as out:
                for chunk in response.iter_content(1024 * 1024):
                    if chunk:
                        out.write(chunk)
        ext = infer_ext(url, ctype, target.suffix or ".bin")
        if target.suffix.lower() != ext:
            renamed = target.with_suffix(ext)
            target.rename(renamed)
            target = renamed
        return {"ok": True, "url": url, "file": str(target), "size": target.stat().st_size, "content_type": ctype}
    except Exception as exc:
        return {"ok": False, "url": url, "file": str(target), "error": str(exc)}


def media_urls(detail: dict[str, Any]) -> list[tuple[str, str, str]]:
    result: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    video = detail.get("video") if isinstance(detail.get("video"), dict) else {}
    for key in ("playAddr", "playAddrH265", "play_addr", "download_addr"):
        url = first_url(video.get(key))
        if url and url not in seen:
            seen.add(url)
            result.append(("视频", url, ".mp4"))
            break
    for index, image in enumerate(detail.get("images") or [], start=1):
        if not isinstance(image, dict):
            continue
        url = first_url(image.get("urlList") or image.get("url_list") or image)
        if url and url not in seen:
            seen.add(url)
            result.append((f"图片_{index:03d}", url, ".webp"))
        live = first_url(image.get("video"))
        if live and live not in seen:
            seen.add(live)
            result.append((f"实况视频_{index:03d}", live, ".mp4"))
    return result


def build_archive(detail: dict[str, Any], comments: list[dict[str, Any]], diagnostics: dict[str, Any]) -> Path:
    dump(RAW / "001_作品完整信息.json", detail)
    dump(RAW / "全部评论及回复.json", comments)
    dump(DEBUG / "签名接口分页诊断.json", diagnostics)

    author = detail.get("authorInfo") if isinstance(detail.get("authorInfo"), dict) else {}
    work_time = fmt_ts(detail.get("createTime"), "作品时间未知")
    title = str(detail.get("desc") or f"作品_{AWEME_ID}")
    session = requests.Session()
    results: list[dict[str, Any]] = []

    for index, (kind, url, ext) in enumerate(media_urls(detail), start=1):
        target = MEDIA / f"001_{work_time}_{index:03d}_{safe(kind)}{ext}"
        results.append(download(session, url, target))

    selected = [c for c in comments if is_author(c, author) and comment_images(c)]
    selected.sort(key=lambda c: int(c.get("create_time") or c.get("createTime") or 0))
    manifest: list[dict[str, Any]] = []
    for number, comment in enumerate(selected, start=2):
        ctime = fmt_ts(comment.get("create_time") or comment.get("createTime"), "评论时间未知")
        text = str(comment.get("text") or comment.get("content") or "图片评论")
        urls = comment_images(comment)
        entry = {
            "编号": number,
            "评论ID": str(comment.get("cid") or comment.get("comment_id") or ""),
            "层级": int(comment.get("_level") or comment.get("level") or 1),
            "父评论ID": comment.get("_parent_comment_id"),
            "发布时间": ctime,
            "评论文字": text,
            "图片数量": len(urls),
            "下载": [],
            "原始数据": comment,
        }
        for image_index, url in enumerate(urls, start=1):
            target = COMMENT_MEDIA / f"{number:03d}_{ctime}_{image_index:02d}_{safe(text, 48)}.jpg"
            record = download(session, url, target)
            entry["下载"].append(record)
            results.append(record)
        manifest.append(entry)

    dump(ROOT / "作者图片评论清单.json", manifest)
    dump(ROOT / "下载结果.json", results)
    times = [m["发布时间"] for m in manifest]
    comment_range = "评论时间未知" if not times else times[0] if len(times) == 1 else f"{times[0]}至{times[-1]}"
    summary = [
        "抖音公开作品存档",
        "编号：001",
        f"作品ID：{AWEME_ID}",
        f"原分享链接：{SHORT_URL}",
        f"作者：{author.get('nickname') or '作者未知'}",
        f"作者UID：{author.get('uid') or detail.get('authorUserId')}",
        f"作品发布时间：{work_time}",
        f"标题：{title}",
        f"作品媒体文件数：{len(media_urls(detail))}",
        f"抓取评论及回复总数：{len(comments)}",
        f"作者本人图片评论数：{len(manifest)}",
        "筛选规则：评论用户 UID 与作品作者 UID 一致，或接口标记为作者。",
    ]
    (ROOT / "000_存档说明.txt").write_text("\n".join(summary), encoding="utf-8")

    name = safe(f"001_作品_{work_time}__作者图片评论_{comment_range}_{AWEME_ID}", 190) + ".zip"
    archive = ARTIFACT / name
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for file in sorted(ROOT.rglob("*")):
            if file.is_file():
                zf.write(file, file.relative_to(ROOT.parent))
    return archive


def main() -> int:
    detail, initial_comments, cookies = parse_source_bundle()
    dump(RAW / "页面首屏评论.json", initial_comments)
    try:
        comments, diagnostics = asyncio.run(fetch_all_comments(cookies))
    except Exception as exc:
        (DEBUG / "评论接口错误.txt").write_text(repr(exc), encoding="utf-8")
        comments = initial_comments
        diagnostics = {"fatal_error": repr(exc), "used_page_comments_only": True}
    archive = build_archive(detail, comments, diagnostics)
    print(f"ARCHIVE_PATH={archive}")
    print(f"ARCHIVE_SIZE={archive.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
