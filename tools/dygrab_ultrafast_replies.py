import asyncio,json,os,re,zipfile
from pathlib import Path
from datetime import datetime,timezone,timedelta
import requests
from playwright.async_api import async_playwright
A='7666692683432429819'; UID='717344312142287'; SEC='MS4wLjABAAAASUWIr_SDLMfbpb6mbeGrx_5fKU6NG7z1msxOWgiwYwU'; TZ=timezone(timedelta(hours=8))
O=Path('ultra_reply_out'); (O/'作者图片回复').mkdir(parents=True,exist_ok=True); (O/'raw').mkdir(exist_ok=True)
P=[]
def cks(s):
 r=[]
 for x in s.split(';'):
  if '=' in x:
   n,v=x.strip().split('=',1); r.append({'name':n,'value':v,'domain':'.douyin.com','path':'/','secure':True,'sameSite':'Lax'})
 return r
def author(c):
 u=c.get('user') or {}; return str(u.get('uid') or '')==UID or str(u.get('sec_uid') or '')==SEC
def images(c):
 for k in ('image_list','images','image_comment'):
  v=c.get(k)
  if isinstance(v,list):return [x for x in v if isinstance(x,dict)]
 return []
def urls(e):
 a=[];b=[]
 for k in ('origin_url','download_url','medium_url','crop_url','thumb_url'):
  v=e.get(k)
  if isinstance(v,dict):
   for u in v.get('url_list') or v.get('urlList') or []:
    if isinstance(u,str) and u.startswith('http'):(a if k=='origin_url' else b).append(u)
 return list(dict.fromkeys(a or b))
def stamp(t):
 try:return datetime.fromtimestamp(int(t),TZ).strftime('%Y-%m-%d_%H-%M-%S')
 except:return '时间未知'
def safe(s):return (re.sub(r'[\\/:*?"<>|\r\n]+','_',s or '').strip(' ._')[:36] or '无文字')
async def main():
 L=json.load(open(os.environ['LOGIN_JSON'],encoding='utf8')); ua=L['user_agent']; expected={str(x['cid']):int(x.get('reply_comment_total') or 0) for x in json.load(open('reply_capture/top_comments_min.json',encoding='utf8'))}
 tasks=set(); idx=0; tops=set(); byparent={}; reached=False
 async def cap(r):
  nonlocal idx,reached
  if '/aweme/v1/web/comment/list' not in r.url:return
  try:d=await r.json()
  except:return
  idx+=1; rec={'url':r.url,'data':d}; P.append(rec); (O/'raw'/f'{idx:04d}.json').write_text(json.dumps(rec,ensure_ascii=False),encoding='utf8')
  if '/reply/' in r.url:
   m=re.search(r'[?&]comment_id=(\d+)',r.url); parent=m.group(1) if m else ''
   q=byparent.setdefault(parent,{})
   for c in d.get('comments') or []:
    if isinstance(c,dict) and c.get('cid'):q[str(c['cid'])]=c
  else:
   for c in d.get('comments') or []:
    if isinstance(c,dict) and c.get('cid'):tops.add(str(c['cid']))
   if int(d.get('has_more') or 0)==0 and len(tops)>=200:reached=True
 def onresp(r):
  t=asyncio.create_task(cap(r));tasks.add(t);t.add_done_callback(tasks.discard)
 async with async_playwright() as p:
  b=await p.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-blink-features=AutomationControlled'])
  ctx=await b.new_context(user_agent=ua,viewport={'width':1920,'height':1080},locale='zh-CN',timezone_id='Asia/Shanghai');await ctx.add_cookies(cks(L['cookie']))
  page=await ctx.new_page();page.on('response',onresp);await page.goto(f'https://www.douyin.com/note/{A}',wait_until='domcontentloaded',timeout=120000);await page.wait_for_timeout(10000)
  body=await page.locator('body').inner_text();blocked=any(x in body for x in ('扫码登录','登录后即可查看更多评论','验证码登录'))
  tab=False
  try:
   q=page.get_by_text(re.compile(r'^评论\(\d+\)$'))
   if await q.count():await q.first.click(force=True,timeout=5000);tab=True;await page.wait_for_timeout(6000)
  except:pass
  await page.screenshot(path=str(O/'01_评论标签.png'))
  stale=0;prev=(-1,-1,-1);progress=[]
  for step in range(480):
   targets=await page.evaluate(r'''() => {
    window.__demuSeen=window.__demuSeen||new Set();window.__demuSeq=window.__demuSeq||0;
    const rx=/(展开\s*\d*\s*条?回复|查看\s*\d*\s*条?回复|更多回复|查看全部回复|展开更多回复|查看更多回复)/;const out=[];
    for(const el of document.querySelectorAll('button')){
     const bt=(el.innerText||el.textContent||'').replace(/\s+/g,' ').trim();if(!rx.test(bt)||bt.length>50)continue;
     const r=el.getBoundingClientRect();if(r.width<2||r.height<2||r.bottom<0||r.top>innerHeight)continue;
     let p=el,ctx='';for(let i=0;i<8&&p;i++,p=p.parentElement){const t=(p.innerText||'').replace(/\s+/g,' ').trim();if(t.length>20&&t.length<2500){ctx=t.slice(0,1400);break}}
     const key=bt+'|'+ctx;if(window.__demuSeen.has(key))continue;window.__demuSeen.add(key);const id='d'+(++window.__demuSeq);el.setAttribute('data-demu-ultra',id);out.push(id);
    }return out.slice(0,30);
   }''')
   clicked=0
   for ident in targets:
    try:
     q=page.locator(f'[data-demu-ultra="{ident}"]');await q.click(force=True,timeout=1800);clicked+=1;await page.wait_for_timeout(180)
    except:pass
   moved=await page.evaluate(r'''() => {const a=[...document.querySelectorAll('*')].filter(e=>{const s=getComputedStyle(e),r=e.getBoundingClientRect();return r.width>240&&r.height>180&&r.bottom>0&&r.top<innerHeight&&/(auto|scroll)/.test(s.overflowY)&&e.scrollHeight>e.clientHeight+100}).sort((a,b)=>b.clientWidth*b.clientHeight-a.clientWidth*a.clientHeight);let n=0;for(const e of a.slice(0,10)){const x=e.scrollTop;e.scrollBy(0,Math.max(650,e.clientHeight*.78));if(e.scrollTop!=x)n++}window.scrollBy(0,850);return n}''')
   await page.wait_for_timeout(520)
   if tasks:await asyncio.gather(*list(tasks),return_exceptions=True)
   ru=len({x for q in byparent.values() for x in q});metric=(len(tops),ru,sum(len(q) for q in byparent.values()))
   stale=stale+1 if metric==prev and clicked==0 else 0;prev=metric
   if step%10==0:
    x={'step':step,'top':len(tops),'reply':ru,'parents':len(byparent),'clicked':clicked,'reached':reached,'stale':stale,'moved':moved};progress.append(x);print(json.dumps(x,ensure_ascii=False),flush=True)
   if reached and step>80 and stale>=45:break
  await page.wait_for_timeout(4000)
  if tasks:await asyncio.gather(*list(tasks),return_exceptions=True)
  await page.screenshot(path=str(O/'02_抓取结束.png'));browser_cookies=await ctx.cookies();await b.close()
 replies={}
 for q in byparent.values():replies.update(q)
 ar=[c for c in replies.values() if author(c)];ai=[c for c in ar if images(c)]
 ses=requests.Session();ses.headers.update({'user-agent':ua,'referer':f'https://www.douyin.com/note/{A}'})
 for c in browser_cookies:
  try:ses.cookies.set(c['name'],c['value'],domain=c.get('domain'))
  except:pass
 man=[]
 for no,c in enumerate(sorted(ai,key=lambda x:int(x.get('create_time') or 0)),start=32):
  it={'序号':no,'评论ID':str(c.get('cid') or ''),'时间':stamp(c.get('create_time')),'文字':str(c.get('text') or ''),'文件':[]}
  for j,e in enumerate(images(c),1):
   ok=False;err='no url'
   for u in urls(e):
    try:
     r=ses.get(u,timeout=60);r.raise_for_status();ct=r.headers.get('content-type','').lower();ext='.png' if 'png' in ct else '.webp' if 'webp' in ct else '.gif' if 'gif' in ct else '.jpg';name=f'{no:03d}_{it["时间"]}_{j:02d}_{safe(it["文字"])}{ext}';(O/'作者图片回复'/name).write_bytes(r.content);it['文件'].append({'name':name,'bytes':len(r.content)});ok=True;break
    except Exception as ex:err=repr(ex)
   if not ok:it['文件'].append({'name':None,'error':err})
  man.append(it)
 expected_total=sum(expected.values());complete=sum(len(byparent.get(k,{}))>=v for k,v in expected.items())
 st={'aweme_id':A,'login_blocked':blocked,'comment_tab_clicked':tab,'top_unique':len(tops),'top_reached_end':reached,'target_parents':len(expected),'expected_reply_total':expected_total,'parents_with_reply_payload':len(byparent),'complete_parents':complete,'reply_unique':len(replies),'author_replies':len(ar),'author_image_replies':len(ai),'author_image_files':sum(bool(f.get('name')) for m in man for f in m['文件']),'payloads':len(P),'progress':progress,'captured_at':datetime.now(TZ).isoformat()}
 for n,v in [('全部二级回复.json',list(replies.values())),('作者二级回复.json',ar),('作者图片回复清单.json',man),('按一级评论分组的回复.json',byparent),('抓取统计.json',st)]: (O/n).write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf8')
 with zipfile.ZipFile(f'douyin-ultra-replies-{A}.zip','w',zipfile.ZIP_DEFLATED) as z:
  for f in O.rglob('*'):
   if f.is_file():z.write(f,f)
 print(json.dumps(st,ensure_ascii=False),flush=True)
asyncio.run(main())
