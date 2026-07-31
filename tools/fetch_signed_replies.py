import asyncio, json, os, re, sys, zipfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

A='7666692683432429819'
UID='717344312142287'
SEC='MS4wLjABAAAASUWIr_SDLMfbpb6mbeGrx_5fKU6NG7z1msxOWgiwYwU'
TZ=timezone(timedelta(hours=8))
O=Path('reply_out'); O.mkdir(exist_ok=True); (O/'作者图片回复').mkdir(exist_ok=True); (O/'raw').mkdir(exist_ok=True)
sys.path.insert(0,str(Path('vendor/core').resolve()))
sys.path.insert(0,str(Path('vendor').resolve()))
import api_client as api_module
from api_client import DouyinAPIClient


def parse_cookies(raw):
    out={}
    for part in raw.split(';'):
        if '=' in part:
            k,v=part.strip().split('=',1); out[k]=v
    return out

def author(c):
    u=c.get('user') or {}
    return str(u.get('uid') or '')==UID or str(u.get('sec_uid') or '')==SEC

def images(c):
    for k in ('image_list','images','image_comment'):
        v=c.get(k)
        if isinstance(v,list): return [x for x in v if isinstance(x,dict)]
    return []
def urls(e):
    preferred=[]; other=[]
    for k in ('origin_url','download_url','medium_url','crop_url','thumb_url'):
        v=e.get(k)
        if not isinstance(v,dict): continue
        q=v.get('url_list') or v.get('urlList') or []
        if isinstance(q,list):
            for x in q:
                if isinstance(x,str) and x.startswith('http'):
                    (preferred if k=='origin_url' else other).append(x)
    return list(dict.fromkeys(preferred or other))
def stamp(v):
    try:return datetime.fromtimestamp(int(v),TZ).strftime('%Y-%m-%d_%H-%M-%S')
    except:return '时间未知'
def safe(s):return (re.sub(r'[\\/:*?"<>|\r\n]+','_',s or '').strip(' ._')[:36] or '无文字')

async def main():
    login=json.load(open(os.environ['LOGIN_JSON'],encoding='utf-8'))
    cookies=parse_cookies(login['cookie']); ua=login['user_agent']
    api_module._USER_AGENT_POOL[:]=[ua]
    targets=json.load(open('reply_capture/top_comments_min.json',encoding='utf-8'))
    sem=asyncio.Semaphore(3); results={}; failures=[]
    async with DouyinAPIClient(cookies) as client:
        async def one(t):
            cid=str(t['cid']); expected=int(t.get('reply_comment_total') or 0)
            async with sem:
                cursor=0; got={}; pages=[]
                for page_no in range(20):
                    try:
                        page=await client.get_aweme_comment_replies(aweme_id=A,comment_id=cid,cursor=cursor,count=20)
                    except Exception as ex:
                        failures.append({'cid':cid,'page':page_no,'cursor':cursor,'error':repr(ex)}); break
                    raw=page.get('raw') if isinstance(page.get('raw'),dict) else {}
                    items=page.get('items') or []
                    pages.append({'cursor_in':cursor,'cursor_out':page.get('max_cursor'),'has_more':page.get('has_more'),'status_code':page.get('status_code'),'count':len(items),'raw':raw})
                    for c in items:
                        if isinstance(c,dict) and c.get('cid'):got[str(c['cid'])]=c
                    nxt=int(page.get('max_cursor') or 0); more=bool(page.get('has_more'))
                    print(json.dumps({'cid':cid,'page':page_no,'count':len(items),'total_got':len(got),'expected':expected,'has_more':more,'next':nxt},ensure_ascii=False),flush=True)
                    if not more or not items:break
                    if nxt==cursor:break
                    cursor=nxt
                    await asyncio.sleep(.15)
                results[cid]={'expected':expected,'embedded_cids':t.get('embedded_cids') or [],'items':list(got.values()),'pages':pages}
        for start in range(0,len(targets),12):
            await asyncio.gather(*(one(t) for t in targets[start:start+12]))
            await asyncio.sleep(.5)

        dedup={}
        for v in results.values():
            for c in v['items']:
                if c.get('cid'):dedup[str(c['cid'])]=c
        all_replies=list(dedup.values())
        author_replies=[c for c in all_replies if author(c)]
        author_image_replies=[c for c in author_replies if images(c)]
        manifest=[]
        session=await client.get_session()
        for no,c in enumerate(sorted(author_image_replies,key=lambda x:int(x.get('create_time') or 0)),start=32):
            item={'序号':no,'评论ID':str(c.get('cid') or ''),'时间':stamp(c.get('create_time')),'文字':str(c.get('text') or ''),'文件':[]}
            for image_no,e in enumerate(images(c),start=1):
                ok=False; err='no url'
                for url in urls(e):
                    try:
                        async with session.get(url,headers={'Referer':f'https://www.douyin.com/note/{A}','User-Agent':ua}) as resp:
                            raw=await resp.read()
                            if resp.status>=400 or not raw: raise RuntimeError(f'HTTP {resp.status}')
                            ct=(resp.headers.get('content-type') or '').lower(); ext='.png' if 'png' in ct else '.webp' if 'webp' in ct else '.gif' if 'gif' in ct else '.jpg'
                            name=f'{no:03d}_{item["时间"]}_{image_no:02d}_{safe(item["文字"])}{ext}'
                            (O/'作者图片回复'/name).write_bytes(raw); item['文件'].append({'name':name,'bytes':len(raw),'url':url}); ok=True; break
                    except Exception as ex:err=repr(ex)
                if not ok:item['文件'].append({'name':None,'error':err})
            manifest.append(item)

    expected=sum(int(t.get('reply_comment_total') or 0) for t in targets)
    embedded=set(x for t in targets for x in (t.get('embedded_cids') or []))
    stats={'aweme_id':A,'targets':len(targets),'expected_reply_total_from_top_comments':expected,'signed_reply_unique':len(all_replies),'embedded_known_unique':len(embedded),'union_reply_unique':len(set(dedup)|embedded),'complete_targets':sum(len(set(str(c.get('cid')) for c in v['items'] if c.get('cid'))|set(v['embedded_cids']))>=v['expected'] for v in results.values()),'author_replies':len(author_replies),'author_image_replies':len(author_image_replies),'author_image_files':sum(bool(f.get('name')) for m in manifest for f in m['文件']),'failures':failures,'captured_at':datetime.now(TZ).isoformat()}
    for name,obj in [('全部签名二级回复.json',all_replies),('作者二级回复.json',author_replies),('作者图片回复清单.json',manifest),('回复抓取明细.json',results),('抓取统计.json',stats)]:
        (O/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
    (O/'000_说明.txt').write_text('登录凭证未写入本包；作者严格按 UID/sec_uid 匹配。\n'+json.dumps(stats,ensure_ascii=False),encoding='utf-8')
    with zipfile.ZipFile(f'douyin-signed-replies-{A}.zip','w',zipfile.ZIP_DEFLATED) as z:
        for f in O.rglob('*'):
            if f.is_file():z.write(f,f)
    print(json.dumps(stats,ensure_ascii=False),flush=True)

asyncio.run(main())
