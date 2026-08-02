#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import douyin_profile_enumerate as base  # noqa: E402

TARGET_ID = "19870927XU"
PROFILE_UID = "141208157956367"
PROFILE_SEC_UID = "MS4wLjABAAAA2XKcRLgWFfEHQ8HPVKuA5W6VKgyaImM9tHPX_wDSVpk"
PROFILE_NICKNAME = "lyx520"
ROOT = Path("douyin_profile_enum")
DIAG = ROOT / "诊断"
ROOT.mkdir(parents=True, exist_ok=True)
DIAG.mkdir(parents=True, exist_ok=True)


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    login_text = Path(".private/douyin_login_curl.txt").read_text(encoding="utf-8")
    _, headers, cookie = base.parse_curl(login_text)
    ua = headers.get("user-agent") or "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/150 Safari/537.36"

    aweme_map: OrderedDict[str, dict[str, Any]] = OrderedDict()
    payloads: list[dict[str, Any]] = []
    diag: dict[str, Any] = {
        "target_id": TARGET_ID,
        "profile_uid": PROFILE_UID,
        "profile_sec_uid": PROFILE_SEC_UID,
        "profile_url": f"https://www.douyin.com/user/{PROFILE_SEC_UID}",
        "direct_profile_mode": True,
        "responses_seen": 0,
        "post_responses": 0,
        "post_has_more_false": False,
        "login_blocker": False,
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=ua,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9"},
        )
        context.add_cookies(base.cookies_for_browser(cookie))
        page = context.new_page()

        def on_response(resp) -> None:
            url = resp.url
            if "douyin.com" not in url:
                return
            ctype = (resp.headers.get("content-type") or "").lower()
            if "json" not in ctype and "/aweme/v1/web/" not in url:
                return
            try:
                payload = resp.json()
            except Exception:
                return
            diag["responses_seen"] += 1
            items = base.extract_awemes(payload, PROFILE_SEC_UID)
            if not items:
                return
            if "/aweme/post/" in url or "aweme/post" in url:
                diag["post_responses"] += 1
                if isinstance(payload, dict) and payload.get("has_more") in (0, False):
                    diag["post_has_more_false"] = True
            payloads.append({"url": url, "payload": payload})
            for item in items:
                aid = base.aweme_id(item)
                if aid:
                    aweme_map[aid] = item

        page.on("response", on_response)
        page.goto(diag["profile_url"], wait_until="domcontentloaded", timeout=120000)
        page.wait_for_timeout(12000)
        try:
            body = page.locator("body").inner_text(timeout=10000)
        except Exception:
            body = ""
        diag["login_blocker"] = any(x in body for x in ("登录后即可", "扫码登录", "验证码登录", "密码登录"))
        page.screenshot(path=str(DIAG / "01_主页初始.png"), full_page=False)
        (DIAG / "主页初始.html").write_text(page.content(), encoding="utf-8")

        last = len(aweme_map)
        idle = 0
        for loop in range(320):
            try:
                state = page.evaluate("""() => {
                  const els=[...document.querySelectorAll('*')].filter(e=>{
                    const s=getComputedStyle(e);
                    return (s.overflowY==='auto'||s.overflowY==='scroll') && e.scrollHeight>e.clientHeight+120 && e.clientHeight>250;
                  });
                  els.sort((a,b)=>(b.scrollHeight*b.clientHeight)-(a.scrollHeight*a.clientHeight));
                  const e=els[0];
                  if(e){
                    e.scrollTop=Math.min(e.scrollHeight,e.scrollTop+Math.max(1200,e.clientHeight*.95));
                    e.dispatchEvent(new Event('scroll',{bubbles:true}));
                    return {found:true,top:e.scrollTop,max:e.scrollHeight-e.clientHeight};
                  }
                  window.scrollBy(0,1500);
                  return {found:false,top:window.scrollY,max:document.documentElement.scrollHeight-innerHeight};
                }""")
            except Exception:
                state = {"top": 0, "max": 1}
            page.mouse.wheel(0, 1700)
            page.wait_for_timeout(700)
            count = len(aweme_map)
            if count == last:
                idle += 1
            else:
                last = count
                idle = 0
            if loop in (30, 80, 150, 240, 319):
                page.screenshot(path=str(DIAG / f"主页滚动_{loop:03d}.png"), full_page=False)
            at_bottom = state.get("top", 0) >= state.get("max", 1) - 10
            if idle >= 36 and (diag["post_has_more_false"] or at_bottom):
                break

        page.screenshot(path=str(DIAG / "99_主页最终.png"), full_page=False)
        (DIAG / "主页最终.html").write_text(page.content(), encoding="utf-8")
        browser.close()

    normalized = [base.normalize_aweme(x) for x in aweme_map.values()]
    normalized.sort(key=lambda x: (x["create_time"], x["aweme_id"]), reverse=True)
    videos = [x for x in normalized if x["is_video"]]
    images = [x for x in normalized if x["is_image_post"] and not x["is_video"]]
    summary = {
        "target_douyin_id": TARGET_ID,
        "cookie_valid": not diag["login_blocker"],
        "profile_url": diag["profile_url"],
        "selected_user": {
            "uid": PROFILE_UID,
            "sec_uid": PROFILE_SEC_UID,
            "unique_id": TARGET_ID,
            "nickname": PROFILE_NICKNAME,
        },
        "all_full_metadata_count": len(normalized),
        "video_count": len(videos),
        "image_post_count": len(images),
        "post_has_more_false": diag["post_has_more_false"],
        "post_response_count": diag["post_responses"],
        "login_blocker": diag["login_blocker"],
    }
    dump(ROOT / "枚举摘要.json", summary)
    dump(ROOT / "全部作品.json", normalized)
    dump(ROOT / "视频下载计划.json", videos)
    dump(ROOT / "图文作品清单.json", images)
    dump(DIAG / "主页响应.json", payloads)
    dump(DIAG / "运行诊断.json", diag)
    (ROOT / "000_说明.txt").write_text(
        "\n".join([
            f"目标抖音号：{TARGET_ID}",
            f"主页：{diag['profile_url']}",
            f"Cookie 登录拦截：{diag['login_blocker']}",
            f"完整作品元数据：{len(normalized)}",
            f"视频作品：{len(videos)}",
            f"图文作品：{len(images)}",
            f"接口 has_more=false：{diag['post_has_more_false']}",
        ]) + "\n",
        encoding="utf-8",
    )
    if diag["login_blocker"]:
        raise RuntimeError("Cookie 登录态被抖音页面拦截")
    if not normalized:
        raise RuntimeError("直接主页模式没有获取到作品元数据")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
