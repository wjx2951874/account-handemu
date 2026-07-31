#!/usr/bin/env python3
from __future__ import annotations
import json,re,shlex,zipfile
from collections import OrderedDict
from datetime import datetime,timezone,timedelta
from pathlib import Path
from urllib.parse import urlparse
import requests
from playwright.sync_api import sync_playwright

AWEME_ID='7666692683432429819'
AUTHOR_UID='717344312142287'
AUTHOR_SEC='MS4wLjABAAAASUWIr_SDLMfbpb6mbeGrx_5fKU6NG7z1msxOWgiwYwU'
ROOT=Path('douyin_result'); RAW=ROOT/'原始数据'; IMG=ROOT/'作者图片评论'; DIAG=ROOT/'诊断'
for p in (ROOT,RAW,IMG,DIAG): p.mkdir(parents=True,exist_ok=True)

def parse_curl(text):
    t=shlex.split(text); url=t[1]; h={}; cookie=''; i=2
    while i<len(t):
        if t[i] in ('-H','--header') and i+1<len(t):
            x=t[i+1]
            if ':' in x:
                k,v=x.split(':',1); h[k.strip().lower()]=v.strip()
            i+=2; continue
        if t[i] in ('-b','--cookie') and i+1<len(t): cookie=t[i+1]; i+=2; continue
        i+=1
    if '/aweme/v1/web/comment/list/' not in url or f'aweme_id={AWEME_ID}' not in url or 'sessionid=' not in cookie:
        raise RuntimeError('登录请求无效或作品ID不一致')
    return url,h,cookie

def pw_cookies(s):
    out=[]
    for x in s.split(';'):
        x=x.strip()
        if '=' not in x: continue
        n,v=x.split('=',1)
        out.append({'name':n,'value':v,'domain':'.douyin.com','path':'/','secure':True,'sameSite':'Lax'})
    return out

def cid(c): return str(c.get('cid') or c.get('comment_id') or c.get('id') or '')
def ids(c):
    u=c.get('user') or {}
    return str(u.get('uid') or u.get('user_id') or ''),str(u.get('sec_uid') or '')
def is_author(c):
    a,b=ids(c); return a==AUTHOR_UID or b==AUTHOR_SEC

def flatten(items):
    out=OrderedDict()
    def add(c,parent=''):
        if not isinstance(c,dict): return
        k=cid(c)
        if not k:return
        if parent:
            c=dict(c); c.setdefault('_parent_cid',parent)
        out[k]=c
        for key in ('reply_comment','reply_comments','replies'):
            for x in c.get(key) or []: add(x,k)
    for x in items:add(x)
    return list(out.values())

def image_entries(c):
    for k in ('image_list','images','image_comment','pictures','photo_list'):
        v=c.get(k)
        if isinstance(v,list): return [x for x in v if isinstance(x,dict)]
    return []
def image_url(x):
    for k in ('origin_url','download_url','large_url','medium_url','thumb_url','url'):
        v=x.get(k)
        if isinstance(v,str) and v.startswith('http'): return v
        if isinstance(v,dict):
            for u in v.get('url_list') or v.get('urls') or []:
                if isinstance(u,str) and u.startswith('http'): return u
            u=v.get('url')
            if isinstance(u,str) and u.startswith('http'): return u
    return ''
def fmt(ts):
    try:
        n=int(ts); n=n//1000 if n>10_000_000_000 else n
        return datetime.fromtimestamp(n,timezone(timedelta(hours=8))).strftime('%Y-%m-%d_%H-%M-%S')
    except:return '时间未知'
def suffix(ct,url):
    ct=(ct or '').lower()
    if 'png' in ct:return '.png'
    if 'webp' in ct:return '.webp'
    if 'gif' in ct:return '.gif'
    if 'jpeg' in ct or 'jpg' in ct:return '.jpg'
    s=Path(urlparse(url).path).suffix.lower(); return s if s in ('.jpg','.jpeg','.png','.webp','.gif') else '.jpg'

def main():
    signed_url,h,cookie=parse_curl(Path('.private/douyin_login_curl.txt').read_text())
    ua=h.get('user-agent','Mozilla/5.0')
    payloads=[]; items=[]; diag={'aweme_id':AWEME_ID,'login_blocker':False,'responses':0,'signed_ok':False,'has_more_false':False}
    try:
        hh=dict(h); hh['cookie']=cookie
        r=requests.get(signed_url,headers=hh,timeout=45); d=r.json()
        diag['signed_http_status']=r.status_code; diag['signed_api_status']=d.get('status_code')
        if isinstance(d.get('comments'),list):
            items.extend(d['comments']); payloads.append({'kind':'signed_exact','data':d}); diag['signed_ok']=True
    except Exception as e: diag['signed_error']=f'{type(e).__name__}: {e}'

    def ingest(url,d):
        if not isinstance(d,dict) or not isinstance(d.get('comments'),list):return
        kind='reply' if '/comment/list/reply/' in url else 'top'
        diag['responses']+=1
        if kind=='top' and d.get('has_more') in (0,False):diag['has_more_false']=True
        items.extend(d['comments'])
        payloads.append({'kind':kind,'cursor':d.get('cursor'),'has_more':d.get('has_more'),'total':d.get('total'),'comments':d['comments']})

    with sync_playwright() as p:
        b=p.chromium.launch(headless=True,args=['--disable-blink-features=AutomationControlled','--no-sandbox','--disable-dev-shm-usage'])
        ctx=b.new_context(user_agent=ua,locale='zh-CN',timezone_id='Asia/Shanghai',viewport={'width':1920,'height':1080},extra_http_headers={'Accept-Language':'zh-CN,zh;q=0.9'})
        ctx.add_cookies(pw_cookies(cookie)); page=ctx.new_page()
        def onresp(resp):
            if '/aweme/v1/web/comment/list/' in resp.url:
                try:ingest(resp.url,resp.json())
                except:pass
        page.on('response',onresp)
        page.goto(f'https://www.douyin.com/note/{AWEME_ID}',wait_until='domcontentloaded',timeout=90000)
        page.wait_for_timeout(10000); page.screenshot(path=str(DIAG/'01_初始页面.png'))
        body=page.locator('body').inner_text(timeout=10000)
        diag['login_blocker']=any(x in body for x in ('登录后即可查看更多评论','扫码登录','验证码登录','密码登录'))
        last=0; idle=0
        for loop in range(280):
            clicked=0; bs=page.locator('button')
            try:n=min(bs.count(),800)
            except:n=0
            for i in range(n):
                try:
                    q=bs.nth(i); txt=(q.inner_text(timeout=180) or '').strip()
                    if re.search(r'(展开|查看|更多).{0,16}回复',txt) and not re.search(r'收起|隐藏',txt) and q.is_visible(timeout=100):
                        q.click(timeout=1200); clicked+=1; page.wait_for_timeout(150)
                except:pass
            try:
                s=page.evaluate("""() => {const es=[...document.querySelectorAll('*')].filter(e=>{const s=getComputedStyle(e);return (s.overflowY==='auto'||s.overflowY==='scroll')&&e.clientHeight>180&&e.scrollHeight>e.clientHeight+80});es.sort((a,b)=>(b.scrollHeight*b.clientHeight)-(a.scrollHeight*a.clientHeight));const e=es[0];if(!e){window.scrollBy(0,1000);return {found:false}};e.scrollTop=Math.min(e.scrollHeight,e.scrollTop+Math.max(1000,e.clientHeight*.9));e.dispatchEvent(new Event('scroll',{bubbles:true}));return {found:true,top:e.scrollTop,max:e.scrollHeight-e.clientHeight}}""")
            except:s={'found':False}
            try:page.mouse.move(1700,850); page.mouse.wheel(0,1500)
            except:pass
            page.wait_for_timeout(650)
            now=len(flatten(items)); idle=idle+1 if now==last and clicked==0 else 0; last=now
            if loop in (40,100,180,260,279):
                try:page.screenshot(path=str(DIAG/f'滚动_{loop:03d}.png'))
                except:pass
            at_bottom=s.get('found') and s.get('top',0)>=s.get('max',1)-8
            if idle>=36 and (at_bottom or diag['has_more_false']):break
        page.screenshot(path=str(DIAG/'99_最终页面.png')); (DIAG/'最终页面.html').write_text(page.content())
        b.close()

    comments=flatten(items); author=[c for c in comments if is_author(c)]; ai=[c for c in author if image_entries(c)]
    (RAW/'全部评论去重.json').write_text(json.dumps(comments,ensure_ascii=False,indent=2))
    (RAW/'作者全部评论.json').write_text(json.dumps(author,ensure_ascii=False,indent=2))
    (RAW/'全部响应.json').write_text(json.dumps(payloads,ensure_ascii=False,indent=2))
    sess=requests.Session(); sess.headers.update({'User-Agent':ua,'Referer':f'https://www.douyin.com/note/{AWEME_ID}','Cookie':cookie})
    manifest=[]; saved=0
    for ni,c in enumerate(sorted(ai,key=lambda x:int(x.get('create_time') or 0)),1):
        rec={'评论ID':cid(c),'时间':fmt(c.get('create_time')),'文字':c.get('text') or '','文件':[]}
        safe=re.sub(r'[\\/:*?"<>|\r\n]+','_',str(c.get('text') or '无文字'))[:30]
        for ii,x in enumerate(image_entries(c),1):
            u=image_url(x)
            if not u:rec['文件'].append({'错误':'无图片URL'});continue
            try:
                rr=sess.get(u,timeout=60); rr.raise_for_status(); name=f'{ni:03d}_{fmt(c.get("create_time"))}_{ii:02d}_{safe}{suffix(rr.headers.get("content-type"),u)}'; (IMG/name).write_bytes(rr.content); rec['文件'].append({'文件名':name,'字节':len(rr.content)}); saved+=1
            except Exception as e:rec['文件'].append({'错误':f'{type(e).__name__}: {e}'})
        manifest.append(rec)
    summary={**diag,'去重评论总数':len(comments),'作者评论数':len(author),'作者图片评论数':len(ai),'作者图片保存数':saved,'清单':manifest}
    (ROOT/'作者图片评论清单.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    (ROOT/'000_抓取说明.txt').write_text('\n'.join([f'作品ID：{AWEME_ID}',f'去重评论总数：{len(comments)}',f'作者评论数：{len(author)}',f'作者图片评论数：{len(ai)}',f'作者图片保存数：{saved}',f'登录拦截：{diag["login_blocker"]}','筛选仅按作者UID/sec_uid精确匹配。','输出中不包含Cookie。']))
    zname=f'douyin_login_result_{AWEME_ID}.zip'
    with zipfile.ZipFile(zname,'w',zipfile.ZIP_DEFLATED) as z:
        for f in ROOT.rglob('*'):
            if f.is_file():z.write(f,f.relative_to(ROOT.parent))
    Path('douyin_run').mkdir(exist_ok=True); (Path('douyin_run')/'result_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print(json.dumps({'zip':zname,'summary':summary},ensure_ascii=False))
if __name__=='__main__':main()
