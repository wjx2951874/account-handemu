import asyncio, json, os, re, zipfile
from pathlib import Path
from datetime import datetime, timezone, timedelta
import requests
from playwright.async_api import async_playwright

A='7666692683432429819'
UID='717344312142287'
SEC='MS4wLjABAAAASUWIr_SDLMfbpb6mbeGrx_5fKU6NG7z1msxOWgiwYwU'
TZ=timezone(timedelta(hours=8))
O=Path('fast_out'); O.mkdir(exist_ok=True); (O/'作者图片评论').mkdir(exist_ok=True); (O/'raw').mkdir(exist_ok=True)
PAYLOADS=[]

def cookie_objects(raw):
    out=[]
    for part in raw.split(';'):
        if '=' not in part: continue
        name,value=part.strip().split('=',1)
        out.append({'name':name,'value':value,'domain':'.douyin.com','path':'/','secure':True,'sameSite':'Lax'})
    return out

def walk(x):
    if isinstance(x,dict):
        if x.get('cid') and isinstance(x.get('user'),dict): yield x
        for v in x.values(): yield from walk(v)
    elif isinstance(x,list):
        for v in x: yield from walk(v)

def author(c):
    u=c.get('user') or {}
    return str(u.get('uid') or '')==UID or str(u.get('sec_uid') or '')==SEC

def image_entries(c):
    for k in ('image_list','images','image_comment'):
        v=c.get(k)
        if isinstance(v,list): return [x for x in v if isinstance(x,dict)]
    return []

def canonical_urls(entry):
    preferred=[]; fallback=[]
    for field in ('origin_url','download_url','medium_url','crop_url','thumb_url'):
        value=entry.get(field)
        if not isinstance(value,dict): continue
        urls=value.get('url_list') or value.get('urlList') or []
        if not isinstance(urls,list): continue
        for url in urls:
            if not isinstance(url,str) or not url.startswith('http'): continue
            (preferred if field=='origin_url' else fallback).append(url)
    if preferred: return list(dict.fromkeys(preferred))
    if fallback: return list(dict.fromkeys(fallback))
    found=[]
    def f(v):
        if isinstance(v,str) and v.startswith('http'): found.append(v)
        elif isinstance(v,dict):
            for q in v.values(): f(q)
        elif isinstance(v,list):
            for q in v: f(q)
    f(entry)
    return list(dict.fromkeys(found))

def stamp(v):
    try: return datetime.fromtimestamp(int(v),TZ).strftime('%Y-%m-%d_%H-%M-%S')
    except: return '时间未知'

def safe(v):
    return (re.sub(r'[\\/:*?"<>|\r\n]+','_',v or '').strip(' ._')[:36] or '无文字')

async def main():
    login=json.load(open(os.environ['LOGIN_JSON'],encoding='utf-8'))
    raw_cookie=login['cookie']; ua=login['user_agent']
    tasks=set(); response_index=0

    async def capture(resp):
        nonlocal response_index
        if '/aweme/v1/web/comment/list' not in resp.url: return
        try: data=await resp.json()
        except: return
        response_index+=1
        record={'url':resp.url,'status':resp.status,'data':data}
        PAYLOADS.append(record)
        (O/'raw'/f'{response_index:04d}.json').write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8')

    def on_response(resp):
        t=asyncio.create_task(capture(resp)); tasks.add(t); t.add_done_callback(tasks.discard)

    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled','--lang=zh-CN'])
        context=await browser.new_context(user_agent=ua,viewport={'width':1920,'height':1080},locale='zh-CN',timezone_id='Asia/Shanghai')
        await context.add_cookies(cookie_objects(raw_cookie))
        page=await context.new_page(); page.on('response',on_response)
        await page.goto(f'https://www.douyin.com/note/{A}',wait_until='domcontentloaded',timeout=120000)
        await page.wait_for_timeout(10000)
        await page.screenshot(path=str(O/'01_初始页面.png'))
        body=await page.locator('body').inner_text(timeout=10000)
        login_blocked=any(x in body for x in ('扫码登录','登录后即可查看更多评论','验证码登录'))
        tab_clicked=False
        try:
            tabs=page.get_by_text(re.compile(r'^评论\(\d+\)$'))
            if await tabs.count():
                await tabs.first.click(force=True,timeout=5000); tab_clicked=True; await page.wait_for_timeout(6000)
        except Exception as ex:
            print('tab_click_error',repr(ex),flush=True)
        await page.screenshot(path=str(O/'02_评论标签.png'))

        stale=0; last=0; click_total=0; progress=[]
        for step in range(360):
            result=await page.evaluate(r'''() => {
              const rx=/(展开\s*\d*\s*条?回复|查看\s*\d*\s*条?回复|更多回复|查看全部回复|展开更多回复|查看更多回复)/;
              let clicked=0, texts=[];
              const nodes=[...document.querySelectorAll('button,[role="button"],span,div')];
              const targets=[];
              for(const node of nodes){
                const text=(node.innerText||node.textContent||'').replace(/\s+/g,' ').trim();
                if(!text || text.length>40 || !rx.test(text)) continue;
                const target=node.closest('button,[role="button"]')||node;
                const r=target.getBoundingClientRect();
                if(r.width<2||r.height<2||r.bottom<0||r.top>innerHeight) continue;
                if(!targets.includes(target)) targets.push(target);
              }
              for(const t of targets){
                try{t.scrollIntoView({block:'center'});t.click();clicked++;texts.push((t.innerText||t.textContent||'').trim())}catch(e){}
              }
              const scrollables=[...document.querySelectorAll('*')].filter(e=>{
                const s=getComputedStyle(e),r=e.getBoundingClientRect();
                return r.width>220&&r.height>160&&r.bottom>0&&r.top<innerHeight&&/(auto|scroll)/.test(s.overflowY)&&e.scrollHeight>e.clientHeight+80;
              }).sort((a,b)=>(b.clientWidth*b.clientHeight)-(a.clientWidth*a.clientHeight));
              let scrolled=[];
              for(const e of scrollables.slice(0,12)){
                const before=e.scrollTop;
                e.scrollBy(0,Math.max(650,e.clientHeight*.88));
                if(e.scrollTop!==before) scrolled.push({h:e.clientHeight,top:e.scrollTop,max:e.scrollHeight-e.clientHeight});
              }
              window.scrollBy(0,950);
              return {clicked,texts,scrolled};
            }''')
            click_total+=int(result.get('clicked') or 0)
            await page.mouse.wheel(0,1700)
            await page.wait_for_timeout(650)
            if tasks: await asyncio.gather(*list(tasks),return_exceptions=True)
            unique={str(c.get('cid')) for x in PAYLOADS for c in walk(x['data']) if c.get('cid')}
            now=len(unique)
            if step%10==0:
                progress.append({'step':step,'unique':now,'payloads':len(PAYLOADS),'clicked':click_total,'last_scrolls':result.get('scrolled')})
                print(json.dumps(progress[-1],ensure_ascii=False),flush=True)
            stale=stale+1 if now<=last and not result.get('clicked') else 0
            last=max(last,now)
            if step>80 and stale>=45: break
        await page.wait_for_timeout(5000)
        if tasks: await asyncio.gather(*list(tasks),return_exceptions=True)
        await page.screenshot(path=str(O/'03_抓取结束.png'))
        cookies=await context.cookies()
        await browser.close()

    dedup={}
    for x in PAYLOADS:
        for c in walk(x['data']):
            if c.get('cid'): dedup[str(c['cid'])]=c
    all_comments=list(dedup.values())
    author_comments=[c for c in all_comments if author(c)]
    author_images=[c for c in author_comments if image_entries(c)]

    session=requests.Session(); session.headers.update({'user-agent':ua,'referer':f'https://www.douyin.com/note/{A}'})
    for ck in cookies:
        try: session.cookies.set(ck['name'],ck['value'],domain=ck.get('domain'))
        except: pass
    manifest=[]
    for number,c in enumerate(sorted(author_images,key=lambda x:int(x.get('create_time') or 0)),start=2):
        item={'序号':number,'评论ID':str(c.get('cid') or ''),'时间':stamp(c.get('create_time')),'文字':str(c.get('text') or ''),'文件':[]}
        for image_no,entry in enumerate(image_entries(c),start=1):
            success=False; error='no url'
            for url in canonical_urls(entry):
                try:
                    r=session.get(url,timeout=60); r.raise_for_status()
                    ct=r.headers.get('content-type','').lower()
                    ext='.png' if 'png' in ct else '.webp' if 'webp' in ct else '.gif' if 'gif' in ct else '.jpg'
                    name=f'{number:03d}_{item["时间"]}_{image_no:02d}_{safe(item["文字"])}{ext}'
                    (O/'作者图片评论'/name).write_bytes(r.content)
                    item['文件'].append({'name':name,'bytes':len(r.content),'url':url}); success=True; break
                except Exception as ex: error=repr(ex)
            if not success: item['文件'].append({'name':None,'error':error})
        manifest.append(item)

    top_ids=set(); reply_ids=set(); totals=[]; reply_declared=0
    for x in PAYLOADS:
        data=x['data']; is_reply='/reply/' in x['url']
        if not is_reply and data.get('total') is not None: totals.append(data.get('total'))
        for c in data.get('comments') or []:
            if not isinstance(c,dict) or not c.get('cid'): continue
            (reply_ids if is_reply else top_ids).add(str(c['cid']))
            if not is_reply: reply_declared+=int(c.get('reply_comment_total') or 0)
    stats={'aweme_id':A,'login_blocked':login_blocked,'comment_tab_clicked':tab_clicked,'payloads':len(PAYLOADS),'top_unique':len(top_ids),'reply_unique':len(reply_ids),'all_unique':len(all_comments),'reply_declared_in_captured_top_pages':reply_declared,'author_comments':len(author_comments),'author_image_comments':len(author_images),'author_image_files':sum(bool(f.get('name')) for m in manifest for f in m['文件']),'api_totals':totals,'click_total':click_total,'progress':progress,'captured_at':datetime.now(TZ).isoformat()}
    for name,value in [('全部评论及回复.json',all_comments),('作者全部评论.json',author_comments),('作者图片评论清单.json',manifest),('抓取统计.json',stats)]:
        (O/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    (O/'000_说明.txt').write_text('登录凭证未写入本包。作者仅按 UID/sec_uid 精确匹配。\n'+json.dumps(stats,ensure_ascii=False),encoding='utf-8')
    archive=f'douyin-fast-comments-{A}.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for f in O.rglob('*'):
            if f.is_file(): z.write(f,f)
    print(json.dumps(stats,ensure_ascii=False),flush=True)

asyncio.run(main())
