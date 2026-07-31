import asyncio, json, os, re, zipfile
from pathlib import Path
from datetime import datetime, timezone, timedelta
import requests
from playwright.async_api import async_playwright

AWEME_ID = '7666692683432429819'
AUTHOR_UID = '717344312142287'
AUTHOR_SEC_UID = 'MS4wLjABAAAASUWIr_SDLMfbpb6mbeGrx_5fKU6NG7z1msxOWgiwYwU'
TZ = timezone(timedelta(hours=8))
OUT = Path('complete_reply_out')
(OUT / '作者图片回复').mkdir(parents=True, exist_ok=True)
(OUT / 'raw').mkdir(parents=True, exist_ok=True)
PAYLOADS = []


def cookie_objects(raw):
    result = []
    for part in raw.split(';'):
        if '=' not in part:
            continue
        name, value = part.strip().split('=', 1)
        result.append({'name': name, 'value': value, 'domain': '.douyin.com', 'path': '/', 'secure': True, 'sameSite': 'Lax'})
    return result


def walk(value):
    if isinstance(value, dict):
        if value.get('cid') and isinstance(value.get('user'), dict):
            yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def is_author(comment):
    user = comment.get('user') or {}
    return str(user.get('uid') or '') == AUTHOR_UID or str(user.get('sec_uid') or '') == AUTHOR_SEC_UID


def image_entries(comment):
    for key in ('image_list', 'images', 'image_comment'):
        value = comment.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def image_urls(entry):
    preferred, fallback = [], []
    for key in ('origin_url', 'download_url', 'medium_url', 'crop_url', 'thumb_url'):
        value = entry.get(key)
        if not isinstance(value, dict):
            continue
        urls = value.get('url_list') or value.get('urlList') or []
        if not isinstance(urls, list):
            continue
        for url in urls:
            if isinstance(url, str) and url.startswith('http'):
                (preferred if key == 'origin_url' else fallback).append(url)
    return list(dict.fromkeys(preferred or fallback))


def timestamp(value):
    try:
        return datetime.fromtimestamp(int(value), TZ).strftime('%Y-%m-%d_%H-%M-%S')
    except Exception:
        return '时间未知'


def safe_name(value):
    return (re.sub(r'[\\/:*?"<>|\r\n]+', '_', value or '').strip(' ._')[:36] or '无文字')


async def main():
    login = json.load(open(os.environ['LOGIN_JSON'], encoding='utf-8'))
    user_agent = login['user_agent']
    expected_targets = {str(item['cid']): int(item.get('reply_comment_total') or 0) for item in json.load(open('reply_capture/top_comments_min.json', encoding='utf-8'))}
    response_tasks = set()
    response_index = 0
    reply_by_parent = {}
    top_ids = set()
    top_reached_end = False

    async def capture_response(response):
        nonlocal response_index, top_reached_end
        url = response.url or ''
        if '/aweme/v1/web/comment/list' not in url:
            return
        try:
            data = await response.json()
        except Exception:
            return
        response_index += 1
        record = {'url': url, 'status': response.status, 'data': data}
        PAYLOADS.append(record)
        (OUT / 'raw' / f'{response_index:04d}.json').write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
        if '/reply/' in url:
            match = re.search(r'[?&]comment_id=(\d+)', url)
            parent = match.group(1) if match else ''
            if parent:
                bucket = reply_by_parent.setdefault(parent, {})
                for comment in data.get('comments') or []:
                    if isinstance(comment, dict) and comment.get('cid'):
                        bucket[str(comment['cid'])] = comment
        else:
            for comment in data.get('comments') or []:
                if isinstance(comment, dict) and comment.get('cid'):
                    top_ids.add(str(comment['cid']))
            try:
                if int(data.get('has_more') or 0) == 0 and len(top_ids) >= 200:
                    top_reached_end = True
            except Exception:
                pass

    def response_handler(response):
        task = asyncio.create_task(capture_response(response))
        response_tasks.add(task)
        task.add_done_callback(response_tasks.discard)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled', '--lang=zh-CN'])
        context = await browser.new_context(user_agent=user_agent, viewport={'width': 1920, 'height': 1080}, locale='zh-CN', timezone_id='Asia/Shanghai')
        await context.add_cookies(cookie_objects(login['cookie']))
        page = await context.new_page()
        page.on('response', response_handler)
        await page.goto(f'https://www.douyin.com/note/{AWEME_ID}', wait_until='domcontentloaded', timeout=120000)
        await page.wait_for_timeout(10000)
        body = await page.locator('body').inner_text(timeout=10000)
        login_blocked = any(text in body for text in ('扫码登录', '登录后即可查看更多评论', '验证码登录'))
        comment_tab_clicked = False
        try:
            tabs = page.get_by_text(re.compile(r'^评论\(\d+\)$'))
            if await tabs.count():
                await tabs.first.click(force=True, timeout=5000)
                comment_tab_clicked = True
                await page.wait_for_timeout(6000)
        except Exception as exc:
            print('comment_tab_click_error', repr(exc), flush=True)
        await page.screenshot(path=str(OUT / '01_评论标签.png'))

        reply_pattern = re.compile(r'(展开\s*\d*\s*条?回复|查看\s*\d*\s*条?回复|更多回复|查看全部回复|展开更多回复|查看更多回复)')
        clicked_keys = set()
        stale_rounds = 0
        previous_metric = (-1, -1, -1)
        progress = []

        for step in range(700):
            clicked_this_round = 0
            buttons = page.locator('button').filter(has_text=reply_pattern)
            try:
                button_count = min(await buttons.count(), 100)
            except Exception:
                button_count = 0
            for index in range(button_count):
                button = buttons.nth(index)
                try:
                    if not await button.is_visible():
                        continue
                    info = await button.evaluate(r'''(el) => {
                      let p = el;
                      for (let i=0; i<8 && p; i++, p=p.parentElement) {
                        const text=(p.innerText||'').replace(/\s+/g,' ').trim();
                        const html=p.outerHTML||'';
                        const ids=html.match(/\d{18,20}/g)||[];
                        if (text.length>20 && text.length<2500) {
                          const avatar=(p.querySelector('img')||{}).src||'';
                          return {buttonText:(el.innerText||el.textContent||'').replace(/\s+/g,' ').trim(), context:text.slice(0,1200), ids:ids.slice(0,8), avatar};
                        }
                      }
                      return {buttonText:(el.innerText||el.textContent||'').trim(),context:'',ids:[],avatar:''};
                    }''')
                    key = json.dumps(info, ensure_ascii=False, sort_keys=True)
                    if key in clicked_keys:
                        continue
                    clicked_keys.add(key)
                    await button.scroll_into_view_if_needed(timeout=1500)
                    await button.click(force=True, timeout=2500)
                    clicked_this_round += 1
                    await page.wait_for_timeout(260)
                except Exception:
                    continue

            try:
                scroll_result = await page.evaluate(r'''() => {
                  const candidates=[...document.querySelectorAll('*')].filter(e=>{
                    const s=getComputedStyle(e), r=e.getBoundingClientRect();
                    return r.width>240 && r.height>180 && r.bottom>0 && r.top<innerHeight && /(auto|scroll)/.test(s.overflowY) && e.scrollHeight>e.clientHeight+100;
                  }).sort((a,b)=>(b.clientWidth*b.clientHeight)-(a.clientWidth*a.clientHeight));
                  const moved=[];
                  for(const e of candidates.slice(0,10)){
                    const before=e.scrollTop;
                    e.scrollBy(0,Math.max(600,e.clientHeight*.72));
                    if(e.scrollTop!==before) moved.push({before,after:e.scrollTop,max:e.scrollHeight-e.clientHeight,height:e.clientHeight});
                  }
                  window.scrollBy(0,800);
                  return moved;
                }''')
            except Exception:
                scroll_result = []
                await page.mouse.wheel(0, 1400)
            await page.wait_for_timeout(650)
            if response_tasks:
                await asyncio.gather(*list(response_tasks), return_exceptions=True)

            reply_unique = len({cid for bucket in reply_by_parent.values() for cid in bucket})
            complete_parents = sum(len(reply_by_parent.get(parent, {})) >= expected for parent, expected in expected_targets.items())
            metric = (len(top_ids), reply_unique, len(clicked_keys))
            if metric == previous_metric and clicked_this_round == 0:
                stale_rounds += 1
            else:
                stale_rounds = 0
            previous_metric = metric
            if step % 10 == 0:
                item = {'step': step, 'top_unique': len(top_ids), 'reply_unique': reply_unique, 'parents_with_reply_payload': len(reply_by_parent), 'complete_parents': complete_parents, 'clicked_keys': len(clicked_keys), 'clicked_round': clicked_this_round, 'top_reached_end': top_reached_end, 'scroll_moved': len(scroll_result), 'stale': stale_rounds}
                progress.append(item)
                print(json.dumps(item, ensure_ascii=False), flush=True)
            if top_reached_end and step > 100 and stale_rounds >= 55:
                break

        await page.wait_for_timeout(5000)
        if response_tasks:
            await asyncio.gather(*list(response_tasks), return_exceptions=True)
        await page.screenshot(path=str(OUT / '02_抓取结束.png'))
        browser_cookies = await context.cookies()
        await browser.close()

    reply_dedup = {}
    for bucket in reply_by_parent.values():
        reply_dedup.update(bucket)
    replies = list(reply_dedup.values())
    author_replies = [item for item in replies if is_author(item)]
    author_image_replies = [item for item in author_replies if image_entries(item)]

    session = requests.Session()
    session.headers.update({'user-agent': user_agent, 'referer': f'https://www.douyin.com/note/{AWEME_ID}'})
    for cookie in browser_cookies:
        try:
            session.cookies.set(cookie['name'], cookie['value'], domain=cookie.get('domain'))
        except Exception:
            pass
    manifest = []
    for number, comment in enumerate(sorted(author_image_replies, key=lambda item: int(item.get('create_time') or 0)), start=32):
        entry = {'序号': number, '评论ID': str(comment.get('cid') or ''), '时间': timestamp(comment.get('create_time')), '文字': str(comment.get('text') or ''), '文件': []}
        for image_number, image in enumerate(image_entries(comment), start=1):
            success = False
            last_error = 'no url'
            for url in image_urls(image):
                try:
                    response = session.get(url, timeout=60)
                    response.raise_for_status()
                    content_type = (response.headers.get('content-type') or '').lower()
                    extension = '.png' if 'png' in content_type else '.webp' if 'webp' in content_type else '.gif' if 'gif' in content_type else '.jpg'
                    filename = f'{number:03d}_{entry["时间"]}_{image_number:02d}_{safe_name(entry["文字"])}{extension}'
                    (OUT / '作者图片回复' / filename).write_bytes(response.content)
                    entry['文件'].append({'name': filename, 'bytes': len(response.content), 'url': url})
                    success = True
                    break
                except Exception as exc:
                    last_error = repr(exc)
            if not success:
                entry['文件'].append({'name': None, 'error': last_error})
        manifest.append(entry)

    expected_total = sum(expected_targets.values())
    complete_parents = sum(len(reply_by_parent.get(parent, {})) >= expected for parent, expected in expected_targets.items())
    stats = {
        'aweme_id': AWEME_ID,
        'login_blocked': login_blocked,
        'comment_tab_clicked': comment_tab_clicked,
        'top_unique': len(top_ids),
        'top_reached_end': top_reached_end,
        'target_parents': len(expected_targets),
        'expected_reply_total': expected_total,
        'parents_with_reply_payload': len(reply_by_parent),
        'complete_parents': complete_parents,
        'reply_unique': len(replies),
        'author_replies': len(author_replies),
        'author_image_replies': len(author_image_replies),
        'author_image_files': sum(bool(file.get('name')) for item in manifest for file in item['文件']),
        'clicked_keys': len(clicked_keys),
        'payloads': len(PAYLOADS),
        'progress': progress,
        'captured_at': datetime.now(TZ).isoformat(),
    }
    for name, value in [('全部二级回复.json', replies), ('作者二级回复.json', author_replies), ('作者图片回复清单.json', manifest), ('按一级评论分组的回复.json', reply_by_parent), ('抓取统计.json', stats)]:
        (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT / '000_说明.txt').write_text('登录凭证未写入本包；作者严格按 UID/sec_uid 匹配。\n' + json.dumps(stats, ensure_ascii=False), encoding='utf-8')
    archive = f'douyin-complete-replies-{AWEME_ID}.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output_zip:
        for file in OUT.rglob('*'):
            if file.is_file():
                output_zip.write(file, file)
    print(json.dumps(stats, ensure_ascii=False), flush=True)


asyncio.run(main())
