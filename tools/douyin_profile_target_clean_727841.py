#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shlex
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

TARGET_AID = "7278410935755148604"
PROFILE_UID = "141208157956367"
PROFILE_SEC_UID = "MS4wLjABAAAA2XKcRLgWFfEHQ8HPVKuA5W6VKgyaImM9tHPX_wDSVpk"
PROFILE_URL = f"https://www.douyin.com/user/{PROFILE_SEC_UID}"
ROOT = Path("douyin_profile_enum")
DIAG = ROOT / "诊断"
ROOT.mkdir(parents=True, exist_ok=True)
DIAG.mkdir(parents=True, exist_ok=True)


def parse_curl(text: str) -> tuple[dict[str, str], str]:
    normalized = text.replace("\\\n", " ").replace("^\n", " ")
    tokens = shlex.split(normalized, posix=True)
    headers: dict[str, str] = {}
    cookie = ""
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in ("-H", "--header") and index + 1 < len(tokens):
            value = tokens[index + 1]
            if ":" in value:
                name, header_value = value.split(":", 1)
                headers[name.strip().lower()] = header_value.strip()
                if name.strip().lower() == "cookie":
                    cookie = header_value.strip()
            index += 2
            continue
        if token in ("-b", "--cookie") and index + 1 < len(tokens):
            cookie = tokens[index + 1]
            index += 2
            continue
        if token in ("-A", "--user-agent") and index + 1 < len(tokens):
            headers["user-agent"] = tokens[index + 1]
            index += 2
            continue
        if token in ("-e", "--referer") and index + 1 < len(tokens):
            headers["referer"] = tokens[index + 1]
            index += 2
            continue
        index += 1
    if not cookie:
        cookie = headers.get("cookie", "")
    if not cookie:
        raise RuntimeError("授权请求中没有 Cookie 请求头")
    return headers, cookie


def browser_cookies(cookie_header: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for part in cookie_header.split(";"):
        if "=" not in part:
            continue
        name, value = part.strip().split("=", 1)
        if not name or re.search(r"\s", name):
            continue
        result.append(
            {
                "name": name,
                "value": value,
                "domain": ".douyin.com",
                "path": "/",
                "secure": True,
                "sameSite": "Lax",
            }
        )
    if not result:
        raise RuntimeError("Cookie 解析后为空")
    return result


def walk(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def item_id(item: dict[str, Any]) -> str:
    return str(item.get("aweme_id") or item.get("group_id") or item.get("item_id") or "")


def author_matches(item: dict[str, Any]) -> bool:
    author = item.get("author")
    if not isinstance(author, dict):
        return True
    sec_uid = str(author.get("sec_uid") or "")
    uid = str(author.get("uid") or "")
    return not sec_uid and not uid or sec_uid == PROFILE_SEC_UID or uid == PROFILE_UID


def extract_awemes(payload: Any) -> list[dict[str, Any]]:
    found: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for node in walk(payload):
        if not isinstance(node, dict):
            continue
        aid = item_id(node)
        if not aid.isdigit() or len(aid) < 15:
            continue
        if not isinstance(node.get("video"), dict):
            continue
        if not author_matches(node):
            continue
        previous = found.get(aid)
        if previous is None or len(json.dumps(node, ensure_ascii=False)) > len(json.dumps(previous, ensure_ascii=False)):
            found[aid] = node
    return list(found.values())


def normalize(item: dict[str, Any]) -> dict[str, Any]:
    create_time = int(item.get("create_time") or 0)
    create_text = ""
    if create_time:
        create_text = datetime.fromtimestamp(create_time, ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "aweme_id": item_id(item),
        "create_time": create_time,
        "create_time_text": create_text,
        "raw": item,
    }


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    login_text = Path(".private/douyin_login_curl.txt").read_text(encoding="utf-8", errors="replace")
    headers, cookie = parse_curl(login_text)
    user_agent = headers.get("user-agent") or "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/150 Safari/537.36"

    state: dict[str, Any] = {
        "target": None,
        "items": OrderedDict(),
        "responses_seen": 0,
        "post_responses": 0,
        "response_urls": [],
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=user_agent,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9"},
        )
        context.add_cookies(browser_cookies(cookie))
        page = context.new_page()

        def on_response(response) -> None:
            url = response.url
            content_type = (response.headers.get("content-type") or "").lower()
            if "douyin.com" not in url:
                return
            if "/aweme/v1/web/" not in url and "json" not in content_type:
                return
            try:
                payload = response.json()
            except Exception:
                return
            state["responses_seen"] += 1
            state["response_urls"].append(url)
            if "aweme/post" in url:
                state["post_responses"] += 1
            for item in extract_awemes(payload):
                aid = item_id(item)
                old = state["items"].get(aid)
                if old is None or len(json.dumps(item, ensure_ascii=False)) > len(json.dumps(old, ensure_ascii=False)):
                    state["items"][aid] = item
                if aid == TARGET_AID:
                    state["target"] = item

        page.on("response", on_response)
        page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=120000)
        page.wait_for_timeout(10000)
        try:
            body_text = page.locator("body").inner_text(timeout=10000)
        except Exception:
            body_text = ""
        login_blocker = any(text in body_text for text in ("登录后即可", "扫码登录", "验证码登录", "密码登录"))
        page.screenshot(path=str(DIAG / "01_主页初始.png"), full_page=False)

        last_count = len(state["items"])
        idle = 0
        for loop in range(360):
            if state["target"] is not None:
                break
            try:
                scroll_state = page.evaluate(
                    """() => {
                      const els=[...document.querySelectorAll('*')].filter(e=>{
                        const s=getComputedStyle(e);
                        return (s.overflowY==='auto'||s.overflowY==='scroll') && e.scrollHeight>e.clientHeight+120 && e.clientHeight>250;
                      });
                      els.sort((a,b)=>(b.scrollHeight*b.clientHeight)-(a.scrollHeight*a.clientHeight));
                      const e=els[0];
                      if(e){
                        e.scrollTop=Math.min(e.scrollHeight,e.scrollTop+Math.max(1300,e.clientHeight*.95));
                        e.dispatchEvent(new Event('scroll',{bubbles:true}));
                        return {found:true,top:e.scrollTop,max:e.scrollHeight-e.clientHeight};
                      }
                      window.scrollBy(0,1600);
                      return {found:false,top:window.scrollY,max:document.documentElement.scrollHeight-innerHeight};
                    }"""
                )
            except Exception:
                scroll_state = {"top": 0, "max": 1}
            page.mouse.wheel(0, 1800)
            page.wait_for_timeout(700)
            count = len(state["items"])
            if count == last_count:
                idle += 1
            else:
                last_count = count
                idle = 0
            if loop in (40, 100, 180, 260, 359):
                page.screenshot(path=str(DIAG / f"主页滚动_{loop:03d}.png"), full_page=False)
            at_bottom = scroll_state.get("top", 0) >= scroll_state.get("max", 1) - 10
            if idle >= 55 and at_bottom:
                break

        page.wait_for_timeout(2500)
        page.screenshot(path=str(DIAG / "99_主页最终.png"), full_page=False)
        browser.close()

    normalized = [normalize(item) for item in state["items"].values()]
    normalized.sort(key=lambda x: (x["create_time"], x["aweme_id"]), reverse=True)
    target = state["target"]
    if target is not None and not any(item["aweme_id"] == TARGET_AID for item in normalized):
        normalized.append(normalize(target))
    dump(ROOT / "全部作品.json", normalized)
    dump(
        DIAG / "运行诊断.json",
        {
            "profile_url": PROFILE_URL,
            "cookie_count": len(browser_cookies(cookie)),
            "login_blocker": login_blocker,
            "responses_seen": state["responses_seen"],
            "post_responses": state["post_responses"],
            "unique_author_video_ids_seen": len(state["items"]),
            "target_found": target is not None,
            "response_urls": list(dict.fromkeys(state["response_urls"]))[-150:],
        },
    )
    if login_blocker:
        raise RuntimeError("登录态被抖音主页拦截")
    if target is None:
        raise RuntimeError(f"主页分页没有命中目标作品 {TARGET_AID}")
    print(
        json.dumps(
            {
                "target_found": True,
                "work_id": TARGET_AID,
                "unique_author_video_ids_seen": len(state["items"]),
                "responses_seen": state["responses_seen"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
