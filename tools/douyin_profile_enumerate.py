from __future__ import annotations
import json
import re
import shlex
import time
from collections import OrderedDict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
TARGET_ID = '19870927XU'
ROOT = Path('douyin_profile_enum')
DIAG = ROOT / '诊断'
ROOT.mkdir(parents=True, exist_ok=True)
DIAG.mkdir(parents=True, exist_ok=True)
TZ = timezone(timedelta(hours=8))

def dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')

def parse_curl(text: str) -> tuple[str, dict[str, str], str]:
    tokens = shlex.split(text)
    if len(tokens) < 2 or tokens[0] != 'curl':
        raise RuntimeError('登录文件不是有效 cURL')
    url = tokens[1]
    headers: dict[str, str] = {}
    cookie = ''
    i = 2
    while i < len(tokens):
        tok = tokens[i]
        if tok in ('-H', '--header') and i + 1 < len(tokens):
            raw = tokens[i + 1]
            if ':' in raw:
                k, v = raw.split(':', 1)
                headers[k.strip().lower()] = v.strip()
            i += 2
            continue
        if tok in ('-b', '--cookie') and i + 1 < len(tokens):
            cookie = tokens[i + 1]
            i += 2
            continue
        i += 1
    if 'sessionid=' not in cookie:
        raise RuntimeError('Cookie 中没有 sessionid')
    return (url, headers, cookie)

def cookies_for_browser(cookie: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for part in cookie.split(';'):
        part = part.strip()
        if not part or '=' not in part:
            continue
        name, value = part.split('=', 1)
        out.append({'name': name, 'value': value, 'domain': '.douyin.com', 'path': '/', 'secure': True, 'sameSite': 'Lax'})
    return out

def walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for v in value.values():
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)

def user_matches(d: dict[str, Any]) -> bool:
    values = {str(d.get('unique_id') or ''), str(d.get('short_id') or ''), str(d.get('douyin_id') or '')}
    return TARGET_ID in values

def extract_user_candidates(payload: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in walk(payload):
        if not isinstance(node, dict):
            continue
        candidates = [node]
        if isinstance(node.get('user_info'), dict):
            candidates.append(node['user_info'])
        if isinstance(node.get('user'), dict):
            candidates.append(node['user'])
        for user in candidates:
            sec = str(user.get('sec_uid') or user.get('sec_user_id') or '')
            if user_matches(user) or (sec and TARGET_ID.lower() in json.dumps(user, ensure_ascii=False).lower()):
                key = sec or str(user.get('uid') or user.get('unique_id') or id(user))
                if key not in seen:
                    seen.add(key)
                    found.append(user)
    return found

def aweme_id(item: dict[str, Any]) -> str:
    return str(item.get('aweme_id') or item.get('group_id') or item.get('item_id') or '')

def extract_awemes(payload: Any, author_sec_uid: str='') -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in walk(payload):
        if not isinstance(node, dict):
            continue
        aid = aweme_id(node)
        if not aid or not aid.isdigit() or len(aid) < 15:
            continue
        has_media = isinstance(node.get('video'), dict) or isinstance(node.get('images'), list) or isinstance(node.get('image_post_info'), dict)
        if not has_media:
            continue
        author = node.get('author') if isinstance(node.get('author'), dict) else {}
        sec = str(author.get('sec_uid') or '')
        if author_sec_uid and sec and (sec != author_sec_uid):
            continue
        if aid not in seen:
            seen.add(aid)
            found.append(node)
    return found

def first_urls(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str) and value.startswith('http'):
        return [value]
    if isinstance(value, list):
        for x in value:
            out.extend(first_urls(x))
    elif isinstance(value, dict):
        for key in ('url_list', 'urls', 'url', 'download_url_list'):
            if key in value:
                out.extend(first_urls(value[key]))
    return list(dict.fromkeys(out))

def video_candidates(item: dict[str, Any]) -> list[dict[str, Any]]:
    video = item.get('video') if isinstance(item.get('video'), dict) else {}
    candidates: list[dict[str, Any]] = []
    for br in video.get('bit_rate') or []:
        if not isinstance(br, dict):
            continue
        addr = br.get('play_addr') or br.get('play_addr_265') or br.get('play_addr_h264')
        urls = first_urls(addr)
        if not urls:
            continue
        candidates.append({'source': 'bit_rate.play_addr', 'bit_rate': int(br.get('bit_rate') or 0), 'quality_type': br.get('quality_type'), 'gear_name': br.get('gear_name'), 'width': int(br.get('play_addr', {}).get('width') or video.get('width') or 0) if isinstance(br.get('play_addr'), dict) else int(video.get('width') or 0), 'height': int(br.get('play_addr', {}).get('height') or video.get('height') or 0) if isinstance(br.get('play_addr'), dict) else int(video.get('height') or 0), 'data_size': int(br.get('play_addr', {}).get('data_size') or br.get('data_size') or 0) if isinstance(br.get('play_addr'), dict) else int(br.get('data_size') or 0), 'urls': urls})
    for key in ('play_addr', 'play_addr_h264', 'play_addr_265'):
        urls = first_urls(video.get(key))
        if urls:
            addr = video.get(key) if isinstance(video.get(key), dict) else {}
            candidates.append({'source': f'video.{key}', 'bit_rate': 0, 'quality_type': None, 'gear_name': None, 'width': int(addr.get('width') or video.get('width') or 0), 'height': int(addr.get('height') or video.get('height') or 0), 'data_size': int(addr.get('data_size') or 0), 'urls': urls})
    for c in candidates:
        c['score'] = int(c.get('bit_rate') or 0) * 100000000 + int(c.get('width') or 0) * int(c.get('height') or 0) * 1000 + int(c.get('data_size') or 0)
        c['looks_watermarked'] = any(('playwm' in u.lower() or 'watermark' in u.lower() for u in c['urls']))
    candidates.sort(key=lambda c: (not c['looks_watermarked'], c['score']), reverse=True)
    return candidates

def fmt_time(ts: Any) -> str:
    try:
        value = int(ts)
        if value > 10000000000:
            value //= 1000
        return datetime.fromtimestamp(value, TZ).strftime('%Y-%m-%d_%H-%M-%S')
    except Exception:
        return '时间未知'

def normalize_aweme(item: dict[str, Any]) -> dict[str, Any]:
    author = item.get('author') if isinstance(item.get('author'), dict) else {}
    video = item.get('video') if isinstance(item.get('video'), dict) else {}
    candidates = video_candidates(item)
    best = candidates[0] if candidates else None
    images = item.get('images') if isinstance(item.get('images'), list) else []
    if not images and isinstance(item.get('image_post_info'), dict):
        images = item['image_post_info'].get('images') or []
    return {'aweme_id': aweme_id(item), 'create_time': int(item.get('create_time') or 0), 'create_time_text': fmt_time(item.get('create_time')), 'desc': str(item.get('desc') or item.get('preview_title') or ''), 'aweme_type': item.get('aweme_type'), 'media_type': item.get('media_type'), 'is_video': bool(video and candidates), 'is_image_post': bool(images), 'duration_ms': int(video.get('duration') or item.get('duration') or 0), 'width': int(video.get('width') or 0), 'height': int(video.get('height') or 0), 'author': {'uid': str(author.get('uid') or ''), 'sec_uid': str(author.get('sec_uid') or ''), 'unique_id': str(author.get('unique_id') or ''), 'short_id': str(author.get('short_id') or ''), 'nickname': str(author.get('nickname') or '')}, 'statistics': item.get('statistics') or {}, 'best_video': best, 'video_candidates': candidates, 'raw': item}

def main() -> None:
    login_text = Path('.private/douyin_login_curl.txt').read_text(encoding='utf-8')
    _, headers, cookie = parse_curl(login_text)
    ua = headers.get('user-agent') or 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/150 Safari/537.36'
    diag: dict[str, Any] = {'target_id': TARGET_ID, 'login_cookie_present': True, 'responses_seen': 0, 'search_responses': 0, 'post_responses': 0, 'post_has_more_false': False, 'login_blocker': False}
    search_payloads: list[Any] = []
    post_payloads: list[Any] = []
    user_candidates: list[dict[str, Any]] = []
    aweme_map: OrderedDict[str, dict[str, Any]] = OrderedDict()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled'])
        context = browser.new_context(user_agent=ua, locale='zh-CN', timezone_id='Asia/Shanghai', viewport={'width': 1920, 'height': 1080}, extra_http_headers={'Accept-Language': 'zh-CN,zh;q=0.9'})
        context.add_cookies(cookies_for_browser(cookie))
        page = context.new_page()
        def on_response(resp) -> None:
            url = resp.url
            if 'douyin.com' not in url:
                return
            content_type = (resp.headers.get('content-type') or '').lower()
            if 'json' not in content_type and '/aweme/v1/web/' not in url:
                return
            try:
                payload = resp.json()
            except Exception:
                return
            diag['responses_seen'] += 1
            users = extract_user_candidates(payload)
            if users:
                user_candidates.extend(users)
            if '/search/' in url or 'search' in url:
                diag['search_responses'] += 1
                if users:
                    search_payloads.append({'url': url, 'payload': payload})
            awemes = extract_awemes(payload)
            if awemes:
                if '/aweme/post/' in url or 'aweme/post' in url:
                    diag['post_responses'] += 1
                    if isinstance(payload, dict) and payload.get('has_more') in (0, False):
                        diag['post_has_more_false'] = True
                post_payloads.append({'url': url, 'payload': payload})
                for item in awemes:
                    aid = aweme_id(item)
                    if aid:
                        aweme_map[aid] = item
        page.on('response', on_response)
        search_url = f'https://www.douyin.com/search/{quote(TARGET_ID)}?type=user'
        page.goto(search_url, wait_until='domcontentloaded', timeout=120000)
        page.wait_for_timeout(12000)
        page.screenshot(path=str(DIAG / '01_搜索页.png'), full_page=False)
        body = ''
        try:
            body = page.locator('body').inner_text(timeout=10000)
        except Exception:
            pass
        diag['login_blocker'] = any((s in body for s in ('登录后即可', '扫码登录', '验证码登录', '密码登录')))
        for _ in range(15):
            page.mouse.wheel(0, 1200)
            page.wait_for_timeout(500)
        visible_user_urls: list[str] = []
        try:
            links = page.locator("a[href*='/user/']")
            for i in range(min(links.count(), 300)):
                a = links.nth(i)
                href = a.get_attribute('href') or ''
                text = ''
                try:
                    text = a.inner_text(timeout=300)
                except Exception:
                    pass
                if href and (TARGET_ID.lower() in text.lower() or TARGET_ID.lower() in href.lower()):
                    visible_user_urls.append(href)
        except Exception:
            pass
        exact_users: list[dict[str, Any]] = []
        seen_users: set[str] = set()
        for u in user_candidates:
            if not user_matches(u):
                continue
            key = str(u.get('sec_uid') or u.get('uid') or u.get('unique_id') or '')
            if key and key not in seen_users:
                seen_users.add(key)
                exact_users.append(u)
        sec_uid = ''
        selected_user: dict[str, Any] = {}
        if exact_users:
            selected_user = exact_users[0]
            sec_uid = str(selected_user.get('sec_uid') or selected_user.get('sec_user_id') or '')
        if not sec_uid:
            for href in visible_user_urls:
                m = re.search('/user/([^/?#]+)', href)
                if m:
                    sec_uid = m.group(1)
                    break
        if not sec_uid:
            try:
                links = page.locator("a[href*='/user/']")
                for i in range(min(links.count(), 500)):
                    a = links.nth(i)
                    text = ''
                    try:
                        text = a.inner_text(timeout=300)
                    except Exception:
                        pass
                    if TARGET_ID.lower() in text.lower():
                        href = a.get_attribute('href') or ''
                        m = re.search('/user/([^/?#]+)', href)
                        if m:
                            sec_uid = m.group(1)
                            break
            except Exception:
                pass
        diag['selected_user'] = {'sec_uid': sec_uid, 'uid': str(selected_user.get('uid') or ''), 'unique_id': str(selected_user.get('unique_id') or ''), 'short_id': str(selected_user.get('short_id') or ''), 'nickname': str(selected_user.get('nickname') or '')}
        dump(ROOT / '搜索用户候选.json', exact_users)
        dump(DIAG / '搜索响应.json', search_payloads)
        if not sec_uid:
            (DIAG / '搜索页.html').write_text(page.content(), encoding='utf-8')
            raise RuntimeError(f'没有在搜索结果中识别到抖音号 {TARGET_ID}')
        profile_url = f'https://www.douyin.com/user/{sec_uid}'
        diag['profile_url'] = profile_url
        page.goto(profile_url, wait_until='domcontentloaded', timeout=120000)
        page.wait_for_timeout(12000)
        page.screenshot(path=str(DIAG / '02_主页初始.png'), full_page=False)
        try:
            html = page.content()
            (DIAG / '主页初始.html').write_text(html, encoding='utf-8')
            for m in re.finditer('"aweme_id"\\s*:\\s*"(\\d{15,22})"', html):
                aid = m.group(1)
                if aid not in aweme_map:
                    aweme_map[aid] = {'aweme_id': aid, '_html_only': True}
        except Exception:
            pass
        last_count = len(aweme_map)
        idle = 0
        for loop in range(260):
            try:
                state = page.evaluate("() => {const els=[...document.querySelectorAll('*')].filter(e=>{const s=getComputedStyle(e); return (s.overflowY==='auto'||s.overflowY==='scroll') && e.scrollHeight>e.clientHeight+120 && e.clientHeight>250;});els.sort((a,b)=>(b.scrollHeight*b.clientHeight)-(a.scrollHeight*a.clientHeight));const e=els[0];if(e){e.scrollTop=Math.min(e.scrollHeight,e.scrollTop+Math.max(1200,e.clientHeight*.95));e.dispatchEvent(new Event('scroll',{bubbles:true}));return {found:true,top:e.scrollTop,max:e.scrollHeight-e.clientHeight};}window.scrollBy(0,1400); return {found:false,top:window.scrollY,max:document.documentElement.scrollHeight-innerHeight};}")
            except Exception:
                state = {'found': False, 'top': 0, 'max': 1}
            page.mouse.wheel(0, 1600)
            page.wait_for_timeout(650)
            count = len(aweme_map)
            if count == last_count:
                idle += 1
            else:
                idle = 0
                last_count = count
            if loop in (20, 60, 120, 200, 259):
                try:
                    page.screenshot(path=str(DIAG / f'主页滚动_{loop:03d}.png'), full_page=False)
                except Exception:
                    pass
            at_bottom = state.get('top', 0) >= state.get('max', 1) - 10
            if idle >= 28 and (diag['post_has_more_false'] or at_bottom):
                break
        try:
            page.screenshot(path=str(DIAG / '99_主页最终.png'), full_page=False)
            (DIAG / '主页最终.html').write_text(page.content(), encoding='utf-8')
        except Exception:
            pass
        browser.close()
    full_items = [v for v in aweme_map.values() if isinstance(v, dict) and (not v.get('_html_only'))]
    html_only_ids = [k for k, v in aweme_map.items() if isinstance(v, dict) and v.get('_html_only')]
    normalized = [normalize_aweme(x) for x in full_items]
    normalized.sort(key=lambda x: (x['create_time'], x['aweme_id']), reverse=True)
    video_items = [x for x in normalized if x['is_video']]
    image_items = [x for x in normalized if x['is_image_post'] and (not x['is_video'])]
    summary = {'target_douyin_id': TARGET_ID, 'cookie_valid': not diag['login_blocker'], 'profile_url': diag.get('profile_url'), 'selected_user': diag.get('selected_user'), 'all_full_metadata_count': len(normalized), 'video_count': len(video_items), 'image_post_count': len(image_items), 'html_only_ids': html_only_ids, 'post_has_more_false': diag['post_has_more_false'], 'post_response_count': diag['post_responses'], 'login_blocker': diag['login_blocker']}
    dump(ROOT / '枚举摘要.json', summary)
    dump(ROOT / '全部作品.json', normalized)
    dump(ROOT / '视频下载计划.json', video_items)
    dump(ROOT / '图文作品清单.json', image_items)
    dump(DIAG / '主页响应.json', post_payloads)
    (ROOT / '000_说明.txt').write_text('\n'.join([f'目标抖音号：{TARGET_ID}', f"Cookie 登录拦截：{diag['login_blocker']}", f"识别主页：{diag.get('profile_url')}", f'完整作品元数据：{len(normalized)}', f'视频作品：{len(video_items)}', f'图文作品：{len(image_items)}', f"接口 has_more=false：{diag['post_has_more_false']}", '视频下载候选仅使用 play_addr/bit_rate.play_addr，不使用通常带水印的 download_addr。']), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))
if __name__ == '__main__':
    main()
