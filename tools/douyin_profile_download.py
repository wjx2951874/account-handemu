from __future__ import annotations
import hashlib
import json
import os
import re
import shlex
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import requests
TARGET_ID = '19870927XU'
PLAN = Path('douyin_profile_enum/视频下载计划.json')
OUT = Path('lyx520_export')
OUT.mkdir(parents=True, exist_ok=True)
LOCK = threading.Lock()

def parse_curl(text: str) -> tuple[dict[str, str], str]:
    tokens = shlex.split(text)
    headers: dict[str, str] = {}
    cookie = ''
    i = 2
    while i < len(tokens):
        if tokens[i] in ('-H', '--header') and i + 1 < len(tokens):
            raw = tokens[i + 1]
            if ':' in raw:
                k, v = raw.split(':', 1)
                headers[k.strip().lower()] = v.strip()
            i += 2
            continue
        if tokens[i] in ('-b', '--cookie') and i + 1 < len(tokens):
            cookie = tokens[i + 1]
            i += 2
            continue
        i += 1
    return (headers, cookie)

def safe_text(value: str) -> str:
    return value.replace('\r\n', '\n').replace('\r', '\n')

def valid_mp4_prefix(prefix: bytes) -> bool:
    return b'ftyp' in prefix[:128] or prefix.startswith(b'\x1aE\xdf\xa3')

def candidate_urls(item: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in item.get('video_candidates') or []:
        if not isinstance(c, dict):
            continue
        if c.get('looks_watermarked'):
            continue
        for url in c.get('urls') or []:
            if not isinstance(url, str) or not url.startswith('http'):
                continue
            lower = url.lower()
            if 'playwm' in lower or 'watermark' in lower:
                continue
            out.append({'url': url, 'source': c.get('source'), 'bit_rate': int(c.get('bit_rate') or 0), 'width': int(c.get('width') or 0), 'height': int(c.get('height') or 0), 'data_size': int(c.get('data_size') or 0), 'score': int(c.get('score') or 0)})
    dedup: dict[str, dict[str, Any]] = {}
    for c in out:
        dedup.setdefault(c['url'], c)
    return sorted(dedup.values(), key=lambda x: (x['score'], x['data_size']), reverse=True)

def download_one(item: dict[str, Any], ua: str, cookie: str) -> dict[str, Any]:
    aweme_id = str(item.get('aweme_id') or '')
    time_text = str(item.get('create_time_text') or '时间未知')
    folder_name = time_text[:16] if time_text != '时间未知' else f'时间未知_{aweme_id}'
    folder = OUT / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    video_path = folder / f'{aweme_id}.mp4'
    desc_path = folder / '简介.txt'
    info_path = folder / '作品信息.json'
    desc = safe_text(str(item.get('desc') or ''))
    desc_path.write_text(desc + ('\n' if desc and (not desc.endswith('\n')) else ''), encoding='utf-8')
    info = {'抖音号': TARGET_ID, '作品ID': aweme_id, '发布时间': time_text, '简介': desc, '时长毫秒': item.get('duration_ms'), '宽': item.get('width'), '高': item.get('height'), '作者': item.get('author'), '统计': item.get('statistics'), '评论PDF': '待后期实现', '下载规则': '仅使用 play_addr/bit_rate.play_addr，排除 download_addr、playwm 和 watermarked URL。'}
    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
    if video_path.exists() and video_path.stat().st_size > 100000:
        return {'ok': True, 'aweme_id': aweme_id, 'folder': folder_name, 'file': str(video_path), 'size': video_path.stat().st_size, 'reused': True}
    headers = {'User-Agent': ua, 'Accept': '*/*', 'Accept-Language': 'zh-CN,zh;q=0.9', 'Accept-Encoding': 'identity', 'Referer': f'https://www.douyin.com/video/{aweme_id}', 'Cookie': cookie, 'Connection': 'keep-alive'}
    session = requests.Session()
    attempts: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidate_urls(item), start=1):
        url = candidate['url']
        part = video_path.with_suffix('.mp4.part')
        try:
            with session.get(url, headers=headers, timeout=(20, 180), stream=True, allow_redirects=True) as response:
                status = response.status_code
                content_type = response.headers.get('content-type', '')
                content_length = int(response.headers.get('content-length') or 0)
                if status >= 400:
                    raise RuntimeError(f'HTTP {status}')
                first = b''
                sha = hashlib.sha256()
                total = 0
                with part.open('wb') as fh:
                    for chunk in response.iter_content(1024 * 1024):
                        if not chunk:
                            continue
                        if len(first) < 128:
                            first += chunk[:128 - len(first)]
                        fh.write(chunk)
                        sha.update(chunk)
                        total += len(chunk)
                if total < 100000:
                    raise RuntimeError(f'响应过小：{total} 字节')
                if not valid_mp4_prefix(first):
                    raise RuntimeError(f'响应不是可识别视频，Content-Type={content_type}')
                part.replace(video_path)
                result = {'ok': True, 'aweme_id': aweme_id, 'folder': folder_name, 'file': str(video_path), 'size': total, 'sha256': sha.hexdigest(), 'content_type': content_type, 'content_length': content_length, 'candidate_index': index, 'candidate': {k: v for k, v in candidate.items() if k != 'url'}, 'final_host': urlparse(response.url).hostname, 'attempts': attempts}
                info['视频文件'] = video_path.name
                info['视频字节'] = total
                info['SHA256'] = result['sha256']
                info['下载候选'] = result['candidate']
                info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
                return result
        except Exception as exc:
            try:
                part.unlink(missing_ok=True)
            except Exception:
                pass
            attempts.append({'index': index, 'host': urlparse(url).hostname, 'source': candidate.get('source'), 'bit_rate': candidate.get('bit_rate'), 'resolution': f"{candidate.get('width')}x{candidate.get('height')}", 'error': f'{type(exc).__name__}: {exc}'})
            time.sleep(min(0.4 * index, 2.0))
    return {'ok': False, 'aweme_id': aweme_id, 'folder': folder_name, 'file': str(video_path), 'attempts': attempts, 'error': '所有无水印播放地址均下载失败'}

def main() -> None:
    if not PLAN.exists():
        raise RuntimeError(f'下载计划不存在：{PLAN}')
    items = json.loads(PLAN.read_text(encoding='utf-8'))
    login_text = Path('.private/douyin_login_curl.txt').read_text(encoding='utf-8')
    headers, cookie = parse_curl(login_text)
    ua = headers.get('user-agent') or 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/150 Safari/537.36'
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(download_one, item, ua, cookie): item for item in items}
        for n, future in enumerate(as_completed(futures), start=1):
            try:
                result = future.result()
            except Exception as exc:
                item = futures[future]
                result = {'ok': False, 'aweme_id': item.get('aweme_id'), 'error': f'{type(exc).__name__}: {exc}'}
            with LOCK:
                results.append(result)
                print(json.dumps({'progress': f'{n}/{len(items)}', 'aweme_id': result.get('aweme_id'), 'ok': result.get('ok'), 'size': result.get('size'), 'error': result.get('error')}, ensure_ascii=False), flush=True)
    results.sort(key=lambda x: x.get('folder') or '', reverse=True)
    ok = [x for x in results if x.get('ok')]
    failed = [x for x in results if not x.get('ok')]
    summary = {'target_douyin_id': TARGET_ID, 'planned_video_count': len(items), 'downloaded_count': len(ok), 'failed_count': len(failed), 'total_bytes': sum((int(x.get('size') or 0) for x in ok)), 'results': results}
    (OUT / '全部作品ID.json').write_text(json.dumps([str(x.get('aweme_id') or '') for x in items], ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT / '下载汇总.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT / '000_说明.txt').write_text('\n'.join([f'目标抖音号：{TARGET_ID}', f'计划下载视频：{len(items)}', f'成功：{len(ok)}', f'失败：{len(failed)}', f"总字节：{summary['total_bytes']}", '目录按作品发布时间（年-月-日_时-分）命名。', '每个目录包含以作品 ID 命名的 MP4、简介.txt 和作品信息.json。', '本批次暂不生成评论 PDF，后期单独补充。', '下载仅选用 play_addr/bit_rate.play_addr，排除常见带水印 download_addr/playwm 地址。']), encoding='utf-8')
    print(json.dumps({k: v for k, v in summary.items() if k != 'results'}, ensure_ascii=False), flush=True)
    if failed:
        raise SystemExit(2)
if __name__ == '__main__':
    main()
