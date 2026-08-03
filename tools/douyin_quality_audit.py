from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Any

import requests
from playwright.sync_api import sync_playwright

AWEME_ID = "7669428426503145343"
KNOWN_VIDEO_ID = "v0d00fg10000d9nkf27og65nf7b9tih0"
OUT = Path("quality_audit")
OUT.mkdir(parents=True, exist_ok=True)


def parse_curl(path: Path) -> tuple[str, dict[str, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    tokens = shlex.split(text.replace("\\\n", " "))
    url = ""
    headers: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in {"-H", "--header"} and i + 1 < len(tokens):
            raw = tokens[i + 1]
            if ":" in raw:
                k, v = raw.split(":", 1)
                headers[k.strip()] = v.strip()
            i += 2
            continue
        if token in {"-b", "--cookie"} and i + 1 < len(tokens):
            headers["Cookie"] = tokens[i + 1]
            i += 2
            continue
        if token.startswith("http://") or token.startswith("https://"):
            url = token
        i += 1
    if not headers.get("User-Agent"):
        headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36"
    return url, headers


def cookie_dict(cookie_header: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in cookie_header.split(";"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        if k.strip():
            out[k.strip()] = v.strip()
    return out


def recursively_collect(obj: Any, path: str = "root", inherited: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    inherited = inherited or {}
    found: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        local = dict(inherited)
        for key in ("width", "height", "data_size", "url_key", "uri", "has_watermark", "bit_rate", "quality_type", "gear_name"):
            if key in obj:
                local[key] = obj.get(key)
        urls = obj.get("url_list")
        if isinstance(urls, list):
            for u in urls:
                if isinstance(u, str) and u.startswith("http"):
                    found.append({"source_path": path, "url": u, **local})
        for key, val in obj.items():
            found.extend(recursively_collect(val, f"{path}.{key}", local))
    elif isinstance(obj, list):
        for idx, val in enumerate(obj):
            found.extend(recursively_collect(val, f"{path}[{idx}]", inherited))
    return found


def ffprobe(path: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height,bit_rate,avg_frame_rate",
            "-show_entries", "format=duration,size,bit_rate",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr[-1000:]}
    data = json.loads(proc.stdout)
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}
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


def looks_watermarked(entry: dict[str, Any]) -> bool:
    text = (entry.get("source_path", "") + " " + entry.get("url", "")).lower()
    if entry.get("has_watermark") is True:
        return True
    return any(x in text for x in ("download_addr", "playwm", "watermark", "suffix_logo"))


def main() -> None:
    _, original_headers = parse_curl(Path(".private/douyin_login_curl.txt"))
    cookie_header = original_headers.get("Cookie") or original_headers.get("cookie") or ""
    ua = original_headers.get("User-Agent") or original_headers.get("user-agent")
    cookies = cookie_dict(cookie_header)

    session = requests.Session()
    session.headers.update({
        "User-Agent": ua,
        "Accept": "*/*",
        "Referer": f"https://www.douyin.com/video/{AWEME_ID}",
    })
    session.cookies.update(cookies)

    captured_json: list[dict[str, Any]] = []
    browser_media_urls: list[str] = []
    diagnostics: dict[str, Any] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            user_agent=ua,
            viewport={"width": 1920, "height": 1080},
            device_scale_factor=2,
            locale="zh-CN",
        )
        pcookies = []
        for k, v in cookies.items():
            pcookies.append({"name": k, "value": v, "domain": ".douyin.com", "path": "/"})
        if pcookies:
            context.add_cookies(pcookies)
        page = context.new_page()

        def on_response(resp: Any) -> None:
            try:
                ctype = (resp.headers.get("content-type") or "").lower()
                if "json" in ctype or any(x in resp.url for x in ("aweme/detail", "aweme/post", "aweme/favorite")):
                    data = resp.json()
                    if isinstance(data, dict):
                        captured_json.append({"url": resp.url, "data": data})
            except Exception:
                pass

        page.on("response", on_response)
        page.goto(f"https://www.douyin.com/video/{AWEME_ID}", wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(12000)
        for label in ("1080P", "超清", "高清", "原画", "2K", "4K"):
            try:
                loc = page.get_by_text(label, exact=True)
                if loc.count() and loc.first.is_visible():
                    loc.first.click(timeout=2500)
                    page.wait_for_timeout(3000)
            except Exception:
                pass
        try:
            browser_media_urls = page.evaluate("""
                () => {
                  const out = new Set();
                  document.querySelectorAll('video').forEach(v => {
                    if (v.currentSrc) out.add(v.currentSrc);
                    if (v.src) out.add(v.src);
                  });
                  performance.getEntriesByType('resource').forEach(e => {
                    if (/video|play|tos-cn|bytecdn|zjcdn/.test(e.name)) out.add(e.name);
                  });
                  return [...out];
                }
            """)
        except Exception:
            browser_media_urls = []
        diagnostics["page_url"] = page.url
        diagnostics["title"] = page.title()
        diagnostics["video_elements"] = page.locator("video").count()
        page.screenshot(path=str(OUT / "page.png"), full_page=False)
        (OUT / "page.html").write_text(page.content(), encoding="utf-8")
        browser.close()

    (OUT / "captured_responses.json").write_text(json.dumps(captured_json, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "browser_media_urls.json").write_text(json.dumps(browser_media_urls, ensure_ascii=False, indent=2), encoding="utf-8")

    entries: list[dict[str, Any]] = []
    for item in captured_json:
        entries.extend(recursively_collect(item["data"], path=f"response:{item['url']}"))
    for u in browser_media_urls:
        if isinstance(u, str) and u.startswith("http"):
            entries.append({"source_path": "browser_resource", "url": u})

    video_ids = {KNOWN_VIDEO_ID}
    for e in entries:
        uri = e.get("uri")
        if isinstance(uri, str) and re.fullmatch(r"v[a-zA-Z0-9]+", uri):
            video_ids.add(uri)
        parsed = urllib.parse.urlparse(e["url"])
        qs = urllib.parse.parse_qs(parsed.query)
        for vid in qs.get("video_id", []):
            if vid:
                video_ids.add(vid)

    generated: list[dict[str, Any]] = []
    hosts = ["www.douyin.com", "aweme.snssdk.com", "api3-normal-c-lq.amemv.com"]
    ratios = ["origin", "4k", "2k", "1080p", "720p"]
    for vid in sorted(video_ids):
        for host in hosts:
            for ratio in ratios:
                u = f"https://{host}/aweme/v1/play/?video_id={vid}&ratio={ratio}&line=0&is_play_url=1"
                generated.append({"source_path": f"generated:{host}:{ratio}", "url": u, "requested_ratio": ratio, "uri": vid})
    entries.extend(generated)

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for e in entries:
        u = e.get("url")
        if not isinstance(u, str) or not u.startswith("http") or u in seen:
            continue
        seen.add(u)
        e["looks_watermarked"] = looks_watermarked(e)
        unique.append(e)

    # Prefer promising candidates first; cap the audit to avoid repeatedly downloading low variants.
    unique.sort(key=lambda e: (
        1 if e.get("looks_watermarked") else 0,
        -(int(e.get("width") or 0) * int(e.get("height") or 0)),
        -int(e.get("bit_rate") or 0),
        0 if str(e.get("source_path", "")).startswith("generated") else 1,
    ))

    results: list[dict[str, Any]] = []
    hashes: set[str] = set()
    max_downloads = 35
    download_count = 0
    for idx, entry in enumerate(unique):
        record = dict(entry)
        try:
            resp = session.get(entry["url"], allow_redirects=True, stream=True, timeout=(20, 90))
            record.update({
                "status": resp.status_code,
                "final_url": resp.url,
                "content_type": resp.headers.get("content-type"),
                "content_length": resp.headers.get("content-length"),
            })
            ctype = (resp.headers.get("content-type") or "").lower()
            if resp.status_code != 200 or ("video" not in ctype and "octet-stream" not in ctype):
                results.append(record)
                resp.close()
                continue
            if download_count >= max_downloads:
                record["skipped_reason"] = "download cap"
                results.append(record)
                resp.close()
                continue
            tmp = OUT / f"candidate_{idx:03d}.bin"
            h = hashlib.sha256()
            total = 0
            with tmp.open("wb") as f:
                for chunk in resp.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > 400 * 1024 * 1024:
                        raise RuntimeError("candidate exceeds 400 MiB")
                    h.update(chunk)
                    f.write(chunk)
            resp.close()
            digest = h.hexdigest()
            if digest in hashes:
                tmp.unlink(missing_ok=True)
                record["duplicate_sha256"] = digest
                results.append(record)
                continue
            hashes.add(digest)
            download_count += 1
            info = ffprobe(tmp)
            suffix = ".mp4" if info.get("ok") else ".bin"
            final = tmp.with_suffix(suffix)
            tmp.rename(final)
            record.update({"sha256": digest, "local_file": final.name, "probe": info})
            results.append(record)
        except Exception as exc:
            record["error"] = repr(exc)
            results.append(record)

    playable = [r for r in results if r.get("probe", {}).get("ok")]
    non_watermarked = [r for r in playable if not r.get("looks_watermarked")]
    ranked = sorted(non_watermarked or playable, key=lambda r: (
        r["probe"].get("width", 0) * r["probe"].get("height", 0),
        r["probe"].get("format_bit_rate", 0),
        r["probe"].get("size", 0),
    ), reverse=True)
    best = ranked[0] if ranked else None
    if best and best.get("local_file"):
        src = OUT / best["local_file"]
        dst = OUT / f"BEST_{AWEME_ID}_{best['probe']['width']}x{best['probe']['height']}.mp4"
        if src != dst:
            dst.write_bytes(src.read_bytes())

    report = {
        "aweme_id": AWEME_ID,
        "known_source_metadata": {"width": 2400, "height": 1080},
        "diagnostics": diagnostics,
        "captured_json_count": len(captured_json),
        "candidate_url_count": len(unique),
        "downloaded_unique_video_count": download_count,
        "best": best,
        "results": results,
    }
    (OUT / "quality_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "captured_json_count": len(captured_json),
        "candidate_url_count": len(unique),
        "downloaded_unique_video_count": download_count,
        "best": best,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
