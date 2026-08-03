#!/usr/bin/env python3
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

import requests
from playwright.sync_api import sync_playwright

TARGET_UID = "141208157956367"
BASE = Path("baseline")
OUT = Path("hq_output")
REPORT = Path("hq_report")
COOKIE_FILE = Path(".private/cookie.txt")
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)


def parse_cookie(cookie_text: str) -> list[dict[str, Any]]:
    cookies: list[dict[str, Any]] = []
    for part in cookie_text.strip().split(";"):
        if "=" not in part:
            continue
        name, value = part.strip().split("=", 1)
        if name:
            cookies.append(
                {
                    "name": name,
                    "value": value,
                    "domain": ".douyin.com",
                    "path": "/",
                    "secure": True,
                }
            )
    return cookies


def aid_of(node: dict[str, Any]) -> str:
    for key in ("aweme_id", "awemeId", "item_id", "itemId", "group_id", "groupId"):
        value = node.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def find_aweme(node: Any, target_id: str, seen: set[int] | None = None) -> dict[str, Any] | None:
    """Recursively find a full aweme object for target_id in arbitrary router/API JSON."""
    if seen is None:
        seen = set()
    if isinstance(node, (dict, list)):
        obj_id = id(node)
        if obj_id in seen:
            return None
        seen.add(obj_id)

    if isinstance(node, dict):
        if aid_of(node) == target_id and isinstance(node.get("video"), dict):
            return node

        priority_keys = (
            "aweme_detail",
            "awemeDetail",
            "item_list",
            "itemList",
            "aweme_list",
            "awemeList",
            "videoInfoRes",
            "video_info_res",
            "detail",
            "data",
            "loaderData",
        )
        for key in priority_keys:
            if key in node:
                found = find_aweme(node[key], target_id, seen)
                if found:
                    return found
        for value in node.values():
            found = find_aweme(value, target_id, seen)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = find_aweme(value, target_id, seen)
            if found:
                return found
    return None


def parse_json_candidate(text: str) -> Any | None:
    possible_texts = [text]
    try:
        decoded = urllib.parse.unquote(text)
        if decoded != text:
            possible_texts.append(decoded)
    except Exception:
        pass

    for candidate in possible_texts:
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except Exception:
            pass

        for marker in (
            "window._ROUTER_DATA =",
            "window._ROUTER_DATA=",
            "self.__next_f.push(",
        ):
            pos = candidate.find(marker)
            if pos < 0:
                continue
            tail = candidate[pos + len(marker) :].strip().rstrip(";")
            if marker.startswith("self.__next_f"):
                tail = tail.rstrip(")")
            try:
                return json.loads(tail)
            except Exception:
                continue
    return None


def extract_aweme_from_page(page: Any, target_id: str, captured: list[Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    debug: dict[str, Any] = {
        "url": page.url,
        "captured_json_count": len(captured),
        "globals_checked": [],
        "script_count": 0,
        "matching_script_ids": [],
    }

    for obj in reversed(captured):
        found = find_aweme(obj, target_id)
        if found:
            debug["source"] = "network_response"
            return found, debug

    for global_name in ("_ROUTER_DATA", "__NEXT_DATA__", "SIGI_STATE", "__INITIAL_STATE__"):
        debug["globals_checked"].append(global_name)
        try:
            obj = page.evaluate(f"() => window.{global_name} || null")
        except Exception as exc:
            debug.setdefault("global_errors", {})[global_name] = str(exc)[:300]
            continue
        found = find_aweme(obj, target_id)
        if found:
            debug["source"] = f"window.{global_name}"
            return found, debug

    try:
        scripts = page.locator("script").evaluate_all(
            """els => els.map(e => ({
                id: e.id || "",
                type: e.type || "",
                text: e.textContent || ""
            }))"""
        )
    except Exception as exc:
        scripts = []
        debug["script_error"] = str(exc)[:500]

    debug["script_count"] = len(scripts)
    for script in scripts:
        text = str(script.get("text") or "")
        script_id = str(script.get("id") or "")
        if target_id not in text and script_id not in ("RENDER_DATA", "__NEXT_DATA__"):
            continue
        debug["matching_script_ids"].append(script_id)
        obj = parse_json_candidate(text)
        found = find_aweme(obj, target_id)
        if found:
            debug["source"] = f"script:{script_id or 'anonymous'}"
            return found, debug

    try:
        html = page.content()
        debug["html_length"] = len(html)
        debug["target_in_html"] = target_id in html
        if target_id in html:
            import re

            match = re.search(
                r'<script[^>]+id=["\']RENDER_DATA["\'][^>]*>(.*?)</script>',
                html,
                flags=re.I | re.S,
            )
            if match:
                obj = parse_json_candidate(match.group(1))
                found = find_aweme(obj, target_id)
                if found:
                    debug["source"] = "html:RENDER_DATA"
                    return found, debug
    except Exception as exc:
        debug["html_error"] = str(exc)[:500]

    return None, debug


def fetch_aweme_detail(page: Any, target_id: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    captured: list[Any] = []
    response_urls: list[str] = []

    def on_response(response: Any) -> None:
        url = response.url
        lowered = url.lower()
        if not any(token in lowered for token in ("aweme", "item", "video", "detail")):
            return
        if len(response_urls) < 100:
            response_urls.append(url.split("?", 1)[0])
        try:
            obj = response.json()
        except Exception:
            return
        if isinstance(obj, (dict, list)):
            captured.append(obj)

    page.on("response", on_response)
    navigation_errors: list[str] = []
    urls = [
        f"https://www.douyin.com/video/{target_id}",
        f"https://www.douyin.com/jingxuan?modal_id={target_id}",
    ]
    try:
        for url in urls:
            captured.clear()
            response_urls.clear()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=90000)
                page.wait_for_timeout(4500)
            except Exception as exc:
                navigation_errors.append(f"{url}: {type(exc).__name__}: {str(exc)[:300]}")
            aweme, debug = extract_aweme_from_page(page, target_id, captured)
            debug["response_url_paths"] = sorted(set(response_urls))[:80]
            debug["navigation_errors"] = navigation_errors
            if aweme:
                return aweme, debug
        return None, {
            "navigation_errors": navigation_errors,
            "last_url": page.url,
            "response_url_paths": sorted(set(response_urls))[:80],
            "captured_json_count": len(captured),
        }
    finally:
        page.remove_listener("response", on_response)


def address_urls(address: Any) -> list[str]:
    if not isinstance(address, dict):
        return []
    values = address.get("url_list") or address.get("urlList") or []
    return [url for url in values if isinstance(url, str) and url.startswith("http")]


def int_value(obj: dict[str, Any], key: str) -> int:
    try:
        return int(obj.get(key) or 0)
    except Exception:
        return 0


def candidates(aweme: dict[str, Any]) -> list[dict[str, Any]]:
    video = aweme.get("video") or {}
    output: list[dict[str, Any]] = []

    def add(
        source: str,
        address: Any,
        bit_rate: Any = 0,
        gear_name: Any = "",
        quality_type: Any = None,
        codec_hint: str = "",
    ) -> None:
        if not isinstance(address, dict):
            return
        urls = address_urls(address)
        if not urls:
            return
        output.append(
            {
                "source": source,
                "urls": urls,
                "width": int_value(address, "width") or int_value(video, "width"),
                "height": int_value(address, "height") or int_value(video, "height"),
                "bit_rate": int(bit_rate or 0),
                "data_size": int_value(address, "data_size") or int_value(address, "dataSize"),
                "gear_name": str(gear_name or ""),
                "quality_type": quality_type,
                "codec_hint": codec_hint,
            }
        )

    for item in video.get("bit_rate") or video.get("bitRate") or []:
        if not isinstance(item, dict):
            continue
        bit_rate = item.get("bit_rate") or item.get("bitRate") or 0
        gear_name = item.get("gear_name") or item.get("gearName") or ""
        quality_type = item.get("quality_type") or item.get("qualityType")
        codec_hint = "h265" if item.get("is_h265") else ("bytevc1" if item.get("is_bytevc1") else "")
        for key in (
            "play_addr",
            "play_addr_265",
            "play_addr_h264",
            "play_addr_bytevc1",
            "play_addr_lowbr",
        ):
            add(
                f"bit_rate.{key}",
                item.get(key),
                bit_rate,
                gear_name,
                quality_type,
                codec_hint or key,
            )

    for key in ("play_addr", "play_addr_265", "play_addr_h264", "play_addr_bytevc1"):
        add(f"video.{key}", video.get(key), codec_hint=key)

    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for candidate in output:
        marker = (
            tuple(candidate["urls"]),
            candidate["width"],
            candidate["height"],
            candidate["bit_rate"],
        )
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(candidate)

    return sorted(
        unique,
        key=lambda c: (
            c["width"] * c["height"],
            c["bit_rate"],
            c["data_size"],
        ),
        reverse=True,
    )


def probe(path: Path) -> dict[str, Any]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height,bit_rate",
        "-show_entries",
        "format=duration,size,bit_rate",
        "-of",
        "json",
        str(path),
    ]
    process = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if process.returncode:
        return {"ok": False, "error": process.stderr[-500:]}
    obj = json.loads(process.stdout)
    stream = (obj.get("streams") or [{}])[0]
    fmt = obj.get("format") or {}
    return {
        "ok": True,
        "codec": stream.get("codec_name"),
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "video_bit_rate": int(stream.get("bit_rate") or 0),
        "format_bit_rate": int(fmt.get("bit_rate") or 0),
        "duration": float(fmt.get("duration") or 0),
        "size": int(fmt.get("size") or path.stat().st_size),
    }


def download(
    session: requests.Session,
    url: str,
    destination: Path,
    cookie_text: str,
) -> dict[str, Any]:
    headers = {
        "User-Agent": UA,
        "Referer": "https://www.douyin.com/",
        "Origin": "https://www.douyin.com",
        "Accept": "*/*",
        "Cookie": cookie_text,
    }
    with session.get(
        url,
        headers=headers,
        stream=True,
        timeout=(20, 180),
        allow_redirects=True,
    ) as response:
        response.raise_for_status()
        content_type = (response.headers.get("content-type") or "").lower()
        with destination.open("wb") as file:
            for chunk in response.iter_content(1024 * 1024):
                if chunk:
                    file.write(chunk)
        if destination.stat().st_size < 5000:
            raise RuntimeError(
                f"文件过小 {destination.stat().st_size}, content-type={content_type}"
            )
        return {
            "status": response.status_code,
            "final_url_host": urllib.parse.urlsplit(response.url).netloc,
            "content_type": content_type,
            "content_length": response.headers.get("content-length"),
        }


def make_targets(
    old_list: list[dict[str, Any]],
    folders: dict[str, str],
) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for old in old_list:
        aid = str(old.get("aweme_id") or "")
        if str((old.get("author") or {}).get("uid")) != TARGET_UID:
            continue
        old_best = old.get("best_video") or {}
        old_width = int(old_best.get("width") or 0)
        old_height = int(old_best.get("height") or 0)
        old_area = old_width * old_height
        old_candidates = old.get("video_candidates") or []
        max_old_area = max(
            (
                int(candidate.get("width") or 0)
                * int(candidate.get("height") or 0)
                for candidate in old_candidates
            ),
            default=0,
        )
        if max_old_area > old_area:
            targets.append(
                {
                    "id": aid,
                    "folder": folders.get(aid),
                    "old_width": old_width,
                    "old_height": old_height,
                    "expected_area": max_old_area,
                }
            )
    return targets


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    cookie_text = COOKIE_FILE.read_text().strip()

    manifest = json.loads((BASE / "归档清单.json").read_text())
    old_list = json.loads((BASE / "全部作品.json").read_text())
    folders = {
        str(record["id"]): record["folder"]
        for record in manifest.get("records", [])
    }
    targets = make_targets(old_list, folders)

    session = requests.Session()
    results: list[dict[str, Any]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=UA,
            locale="zh-CN",
            viewport={"width": 1920, "height": 1080},
        )
        context.add_cookies(parse_cookie(cookie_text))
        page = context.new_page()

        for index, target in enumerate(targets, 1):
            aid = target["id"]
            record = dict(target)
            record["index"] = index
            aweme, page_debug = fetch_aweme_detail(page, aid)
            record["page_debug"] = page_debug
            if not aweme:
                record.update(status="failed", error="detail page did not expose aweme metadata")
                results.append(record)
                print(f"[{index}/{len(targets)}] {aid}: metadata missing", flush=True)
                continue

            current_author_uid = str((aweme.get("author") or {}).get("uid") or "")
            record["fresh_author_uid"] = current_author_uid
            if current_author_uid != TARGET_UID:
                record.update(
                    status="failed",
                    error=f"author UID mismatch: {current_author_uid}",
                )
                results.append(record)
                continue

            stream_candidates = candidates(aweme)
            record["candidate_count"] = len(stream_candidates)
            record["candidate_summary"] = [
                {
                    key: candidate[key]
                    for key in (
                        "source",
                        "width",
                        "height",
                        "bit_rate",
                        "data_size",
                        "gear_name",
                        "codec_hint",
                    )
                }
                for candidate in stream_candidates
            ]
            if not stream_candidates:
                record.update(status="failed", error="no fresh video candidates")
                results.append(record)
                continue

            temp_dir = Path("tmp_downloads") / aid
            temp_dir.mkdir(parents=True, exist_ok=True)
            chosen: dict[str, Any] | None = None
            errors: list[dict[str, Any]] = []
            max_claimed_area = max(
                candidate["width"] * candidate["height"]
                for candidate in stream_candidates
            )

            for candidate_index, candidate in enumerate(stream_candidates):
                claimed_area = candidate["width"] * candidate["height"]
                if (
                    chosen
                    and claimed_area
                    < chosen["probe"]["width"] * chosen["probe"]["height"]
                ):
                    break

                for url_index, url in enumerate(candidate["urls"]):
                    destination = temp_dir / f"{candidate_index:02d}_{url_index:02d}.mp4"
                    try:
                        metadata = download(session, url, destination, cookie_text)
                        actual_probe = probe(destination)
                        if (
                            not actual_probe.get("ok")
                            or not actual_probe.get("width")
                            or not actual_probe.get("height")
                        ):
                            raise RuntimeError(f"ffprobe failed: {actual_probe}")
                        actual_area = (
                            actual_probe["width"] * actual_probe["height"]
                        )
                        possible = {
                            "candidate": candidate,
                            "download": metadata,
                            "probe": actual_probe,
                            "path": str(destination),
                        }
                        if chosen is None or (
                            actual_area,
                            actual_probe.get("format_bit_rate", 0),
                            actual_probe.get("size", 0),
                        ) > (
                            chosen["probe"]["width"] * chosen["probe"]["height"],
                            chosen["probe"].get("format_bit_rate", 0),
                            chosen["probe"].get("size", 0),
                        ):
                            chosen = possible
                        if actual_area >= max_claimed_area:
                            break
                    except Exception as exc:
                        errors.append(
                            {
                                "candidate": candidate_index,
                                "url": url_index,
                                "host": urllib.parse.urlsplit(url).netloc,
                                "error": f"{type(exc).__name__}: {str(exc)[:400]}",
                            }
                        )
                        if destination.exists():
                            destination.unlink()
                if (
                    chosen
                    and chosen["probe"]["width"] * chosen["probe"]["height"]
                    >= max_claimed_area
                ):
                    break

            if not chosen:
                record.update(
                    status="failed",
                    error="all candidates failed",
                    errors=errors,
                )
                results.append(record)
                continue

            actual_area = chosen["probe"]["width"] * chosen["probe"]["height"]
            old_area = int(target["old_width"]) * int(target["old_height"])
            if actual_area <= old_area:
                record.update(
                    status="failed",
                    error="downloaded file is not higher resolution than old file",
                    chosen_probe=chosen["probe"],
                    errors=errors,
                )
                results.append(record)
                continue

            folder = target["folder"] or aid
            final_dir = OUT / folder
            final_dir.mkdir(parents=True, exist_ok=True)
            final_path = final_dir / f"{aid}.mp4"
            shutil.copy2(chosen["path"], final_path)
            sha256 = hashlib.sha256(final_path.read_bytes()).hexdigest()

            record.update(
                status="success",
                new_width=chosen["probe"]["width"],
                new_height=chosen["probe"]["height"],
                codec=chosen["probe"]["codec"],
                duration=chosen["probe"]["duration"],
                size=final_path.stat().st_size,
                sha256=sha256,
                source=chosen["candidate"]["source"],
                gear_name=chosen["candidate"]["gear_name"],
                codec_hint=chosen["candidate"]["codec_hint"],
                errors=errors,
            )
            results.append(record)
            print(
                f"[{index}/{len(targets)}] {aid}: "
                f"{record['old_width']}x{record['old_height']} -> "
                f"{record['new_width']}x{record['new_height']}",
                flush=True,
            )

        state = context.storage_state()
        browser.close()

    summary = {
        "target_uid": TARGET_UID,
        "target_count": len(targets),
        "success_count": sum(result["status"] == "success" for result in results),
        "failed_count": sum(result["status"] != "success" for result in results),
        "browser_cookie_names": [cookie["name"] for cookie in state.get("cookies", [])],
        "results": results,
    }
    (REPORT / "替换报告.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)
    )
    upload_map = [
        {
            "id": result["id"],
            "folder": result["folder"],
            "local": f"hq_output/{result['folder']}/{result['id']}.mp4",
            "width": result.get("new_width"),
            "height": result.get("new_height"),
            "sha256": result.get("sha256"),
            "size": result.get("size"),
        }
        for result in results
        if result["status"] == "success"
    ]
    (REPORT / "上传映射.json").write_text(
        json.dumps(upload_map, ensure_ascii=False, indent=2)
    )
    print(
        json.dumps(
            {
                "target_count": summary["target_count"],
                "success_count": summary["success_count"],
                "failed_count": summary["failed_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if summary["failed_count"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
